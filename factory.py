#!/usr/bin/env python3
"""factory.py - local runner for the LLM app factory.

You write this file into an empty project folder ONCE. It is never emitted or
modified by the LLM. It owns all machinery (validation, git safety, manifest
extraction, context assembly); LLM sessions only ever emit source code and
patches as cheap raw payloads.

Loop:  upload bundle.md -> paste reply into payload.md -> python factory.py apply
Needs: Python >= 3.9, git. No third-party packages.
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import deque
from pathlib import Path

# ----------------------------------------------------------------- constants

ROOT = Path(__file__).resolve().parent
ART = ROOT / "artifacts"
STATE_F = ART / "state.json"
BLUEPRINT_F = ART / "blueprint.md"
MANIFEST_F = ART / "manifest.md"
PAYLOAD_F = ROOT / "payload.md"
BUNDLE_F = ROOT / "bundle.md"
LOG_F = ART / "log.txt"
ERROR_F = ART / "error.md"
REQUEST_F = ART / "request.md"
NOTES_F = ART / "notes.md"
PARTIAL_F = ART / "partial.json"
TEST_OUT_F = ART / "test_out.txt"

MARK = "#%%"
LOG_KEEP = 200          # lines of runtime log kept / shown
SLICE_CTX = 25          # fallback context lines when no enclosing function
SLICE_CAP = 8_000       # max bytes of auto-sliced source per bundle
MAX_SLICES = 6          # deepest distinct frames worth showing
NEED_FILE_CAP = 250     # max lines when a whole file is need-requested
DEFAULT_BUDGET = 350    # target output lines per session

PROTECTED = {"factory.py", "payload.md", "bundle.md", ".env",
             "artifacts/state.json", "artifacts/partial.json"}
EXCLUDE_DIRS = {".git", "artifacts", "node_modules", "__pycache__", ".venv",
                "venv", "dist", "build", ".mypy_cache", ".pytest_cache",
                ".ruff_cache", ".idea", ".vscode"}
SCHEMA_EXT = {".sql", ".prisma", ".proto", ".graphql"}
SIG_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs"}

GITIGNORE = """.env
payload.md
bundle.md
__pycache__/
*.pyc
node_modules/
.venv/
venv/
artifacts/log.txt
artifacts/error.md
artifacts/test_out.txt
artifacts/partial.json
"""

SPEC = """PAYLOAD SPEC v1 - your ENTIRE reply is one payload. No prose outside it.

#%% begin session=<k> blocks=<n>     first line. n = number of blocks below.
#%% file <relative/path>             create/replace whole file; content follows.
#%% patch <relative/path>            edit an existing file with hunks:
<<< find
exact current text (must occur exactly once in the file)
=== replace
new text
>>>
                                     (repeat <<< find / === replace / >>> hunks)
#%% delete <relative/path>           remove a file.
#%% need <path>[:<symbol>] ...       ask for source context; the runner resends
                                     it to you. A need payload must contain only
                                     need and note blocks - nothing else.
#%% note                             free text: memo to the user / next session.
#%% end blocks=<n>                   last line. n must match begin.

Rules
- Verbs are only: file, patch, delete, need, note. blocks counts every block.
- file content is written verbatim plus a trailing newline. patch hunk text is
  matched and inserted exactly as written (no newline added).
- No content line may start with "#%%". To emit one literally write "#%%%" and
  the runner strips one "%".
- patch find-text must be unique in the file. If unsure, resend the whole file.
- Never write secret values; reference environment variable NAMES only.
- If your output is about to be cut off, stop cleanly at a block boundary; the
  runner will ask the next session to resume with:
  #%% begin session=<k> blocks=<remaining> resume=<first missing block index>
"""


# --------------------------------------------------------------------- utils

def _out(msg, err=False):
    try:
        print(msg, file=sys.stderr if err else sys.stdout, flush=True)
    except (BrokenPipeError, OSError):
        pass


def info(msg):
    _out(f"[factory] {msg}")


def die(msg, code=1):
    _out(f"[factory] ERROR: {msg}", err=True)
    sys.exit(code)


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def atomic_write(path: Path, text: str):
    path = path.resolve()
    if ROOT not in path.parents and path != ROOT:
        raise RuntimeError(f"refusing to write outside project root: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (".tmp." + path.name)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_text(path: Path):
    return path.read_text(encoding="utf-8", errors="replace")


def sh(cmd, check=True, capture=True, cwd=None):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=str(cwd or ROOT),
                       capture_output=capture, text=True)
    if check and r.returncode != 0:
        out = ((r.stdout or "") + (r.stderr or "")) if capture else ""
        raise RuntimeError(f"command failed ({cmd}): {out.strip()[:800]}")
    return r


def rel_str(p: Path):
    return p.relative_to(ROOT).as_posix()


def safe_rel(raw: str) -> Path:
    """Validate a payload-supplied relative path. Returns absolute Path."""
    raw = raw.strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"absolute paths not allowed: {raw!r}")
    parts = Path(raw).parts
    if ".." in parts or any(p == ".git" for p in parts):
        raise ValueError(f"illegal path: {raw!r}")
    p = (ROOT / raw).resolve()
    if ROOT not in p.parents and p != ROOT:
        raise ValueError(f"path escapes project root: {raw!r}")
    rel = p.relative_to(ROOT).as_posix()
    if rel in PROTECTED or rel.startswith(".git/"):
        raise ValueError(f"protected path: {rel}")
    return p


def clipboard_text():
    for cmd in (["pbpaste"], ["powershell", "-command", "Get-Clipboard"],
                ["xclip", "-selection", "clipboard", "-o"], ["wl-paste"]):
        if shutil.which(cmd[0]):
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
    die("could not read clipboard; paste the reply into payload.md instead")


# ----------------------------------------------------------------------- git

def git(args, check=True):
    return sh(["git"] + args, check=check)


def ensure_repo():
    if not (ROOT / ".git").exists():
        git(["init", "-q"])
        info("git repo initialized")
    for key, val in (("user.email", "factory@local"), ("user.name", "factory")):
        if sh(["git", "config", key], check=False).returncode != 0:
            git(["config", key, val])


def git_dirty():
    return bool(sh(["git", "status", "--porcelain"]).stdout.strip())


def git_head():
    r = sh(["git", "rev-parse", "HEAD"], check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def git_commit(msg):
    git(["add", "-A"])
    if git_dirty():
        git(["commit", "-q", "-m", msg])
        info(f"committed: {msg}")


def git_rollback(snap):
    if snap:
        git(["reset", "-q", "--hard", snap], check=False)
        git(["clean", "-qfd"], check=False)  # ignored files are kept
        info(f"rolled back to {snap[:10]} (working tree restored)")


# --------------------------------------------------------------------- state

def load_state():
    if STATE_F.exists():
        return json.loads(read_text(STATE_F))
    return None


def save_state(st):
    atomic_write(STATE_F, json.dumps(st, indent=1) + "\n")


def new_state():
    return {"phase": "build", "session_next": 1, "sessions_total": None,
            "fixing": False, "pending_need": [], "resume": None,
            "last_note": "", "history": []}


def load_plan():
    if not BLUEPRINT_F.exists():
        return None
    m = re.search(r"```json\s*(\{.*?\})\s*```", read_text(BLUEPRINT_F), re.S)
    if not m:
        die("blueprint.md has no ```json plan block")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        die(f"blueprint plan JSON invalid: {e}")


def validate_plan(plan):
    for key in ("project", "sessions", "acceptance", "runtime"):
        if key not in plan:
            raise ValueError(f"plan missing key: {key}")
    ses = plan["sessions"]
    n = len(ses) + 1  # +1: session 1 was the blueprint itself
    if not 4 <= n <= 10:
        raise ValueError(f"total sessions must be 4-10, got {n}")
    ids = [s.get("id") for s in ses]
    if ids != list(range(2, n + 1)):
        raise ValueError(f"session ids must be 2..{n} contiguous, got {ids}")
    if not ses[-1].get("verify"):
        raise ValueError('last session must have "verify": true')
    owners = {}
    for s in ses:
        for f in s.get("files", []):
            if f in owners:
                raise ValueError(f"file {f} owned by sessions "
                                 f"{owners[f]} and {s['id']}")
            owners[f] = s["id"]
        for u in s.get("uses", []):
            if not 2 <= u < s["id"]:
                raise ValueError(
                    f"session {s['id']} uses {u}: forward or invalid "
                    "reference - uses may only name EARLIER build sessions")
    return n


# ------------------------------------------------------------ payload parser

BEGIN_RE = re.compile(r"^#%% begin session=(\d+) blocks=(\d+)"
                      r"(?: resume=(\d+))?\s*$")
END_RE = re.compile(r"^#%% end blocks=(\d+)\s*$")
VERB_RE = re.compile(r"^#%% (file|patch|delete|need|note)(?:\s+(.*?))?\s*$")


def unescape(line):
    return "#" + line[2:] if line.startswith("#%%%") else line


def parse_payload(text):
    lines = text.splitlines()
    i, begin = 0, None
    while i < len(lines):
        m = BEGIN_RE.match(lines[i])
        if m:
            begin = m
            break
        i += 1
    if not begin:
        raise ValueError("no '#%% begin session=<k> blocks=<n>' line found")
    session = int(begin.group(1))
    declared = int(begin.group(2))
    resume = int(begin.group(3)) if begin.group(3) else None
    i += 1

    blocks, ended, end_n = [], False, None
    cur = None  # (verb, arg, [content lines])
    while i < len(lines):
        line = lines[i]
        em = END_RE.match(line)
        if em:
            ended, end_n = True, int(em.group(1))
            break
        vm = VERB_RE.match(line)
        if vm:
            if cur:
                blocks.append(cur)
            cur = (vm.group(1), (vm.group(2) or "").strip(), [])
        elif line.startswith("#%%") and not line.startswith("#%%%"):
            raise ValueError(f"unrecognized marker line: {line!r}")
        else:
            if cur is None:
                if line.strip():
                    raise ValueError(f"content before first block: {line!r}")
            else:
                cur[2].append(unescape(line))
        i += 1
    if cur:
        blocks.append(cur)

    return {"session": session, "declared": declared, "resume": resume,
            "blocks": [{"verb": v, "arg": a, "content": "\n".join(c)}
                       for v, a, c in blocks],
            "ended": ended, "end_n": end_n}


HUNK_FIND = re.compile(r"^<{3,7} find\s*$")
HUNK_MID = re.compile(r"^={3,7} replace\s*$")
HUNK_END = re.compile(r"^>{3,7}\s*$")


def parse_hunks(content):
    hunks, mode, find, rep = [], None, [], []
    for line in content.splitlines():
        if HUNK_FIND.match(line):
            if mode is not None:
                raise ValueError("nested '<<< find'")
            mode, find, rep = "find", [], []
        elif HUNK_MID.match(line):
            if mode != "find":
                raise ValueError("'=== replace' without '<<< find'")
            mode = "rep"
        elif HUNK_END.match(line):
            if mode != "rep":
                raise ValueError("'>>>' without '=== replace'")
            hunks.append(("\n".join(find), "\n".join(rep)))
            mode = None
        elif mode == "find":
            find.append(line)
        elif mode == "rep":
            rep.append(line)
        elif line.strip():
            raise ValueError(f"text outside hunks in patch block: {line!r}")
    if mode is not None:
        raise ValueError("unterminated hunk")
    if not hunks:
        raise ValueError("patch block contains no hunks")
    return hunks


# ------------------------------------------------------------------ manifest

def iter_source_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in sorted(dirnames)
                       if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            rel = rel_str(p)
            if rel in PROTECTED or fn.endswith(".pyc") or fn.startswith("."):
                continue
            yield p, rel


def py_signatures(path: Path):
    try:
        tree = ast.parse(read_text(path))
    except SyntaxError as e:
        return [f"!! SYNTAX ERROR: {e}"]
    out = []

    def sig(node, indent=""):
        for d in node.decorator_list:
            try:
                out.append(f"{indent}@{ast.unparse(d)}")
            except Exception:
                pass
        try:
            a = ast.unparse(node.args)
        except Exception:
            a = "..."
        r = ""
        if getattr(node, "returns", None):
            try:
                r = " -> " + ast.unparse(node.returns)
            except Exception:
                pass
        kw = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        out.append(f"{indent}{kw} {node.name}({a}){r}")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig(node)
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            out.append(f"class {node.name}({bases})" if bases
                       else f"class {node.name}")
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sig(sub, "    ")
                elif isinstance(sub, ast.AnnAssign) and \
                        isinstance(sub.target, ast.Name):
                    try:
                        out.append(f"    {sub.target.id}: "
                                   f"{ast.unparse(sub.annotation)}")
                    except Exception:
                        pass
    return out


JS_SIG = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
                    r"(?:function\s+\w+\s*\([^)]*\)|class\s+\w+"
                    r"|const\s+\w+\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)")
JS_ROUTE = re.compile(r"\.(get|post|put|delete|patch)\(\s*['\"]([^'\"]+)")


def js_signatures(path: Path):
    out = []
    for line in read_text(path).splitlines():
        if JS_SIG.match(line):
            out.append(line.strip().rstrip("{").strip())
        for m in JS_ROUTE.finditer(line):
            out.append(f"route {m.group(1).upper()} {m.group(2)}")
    return out


def build_manifest():
    files, ifaces, schemas = [], [], []
    for p, rel in iter_source_files():
        try:
            text = read_text(p)
        except Exception:
            continue
        nlines = text.count("\n") + 1
        files.append(f"{rel} ({nlines} lines, {p.stat().st_size} B)")
        ext = p.suffix.lower()
        if ext == ".py":
            sigs = py_signatures(p)
            if sigs:
                ifaces.append(f"### {rel}\n" + "\n".join(sigs))
        elif ext in SIG_EXT:
            sigs = js_signatures(p)
            if sigs:
                ifaces.append(f"### {rel}\n" + "\n".join(sigs))
        elif ext in SCHEMA_EXT and nlines <= 60:
            schemas.append(f"### {rel}\n{text.rstrip()}")
    out = ["# manifest (machine-extracted ground truth; outranks memory)",
           "", "## files", *(files or ["(none yet)"]), ""]
    if ifaces:
        out += ["## interfaces", *ifaces, ""]
    if schemas:
        out += ["## schemas", *schemas, ""]
    return "\n".join(out)


def refresh_manifest():
    atomic_write(MANIFEST_F, build_manifest())


# ---------------------------------------------------------- context slicing

TB_PY = re.compile(r'File "([^"]+)", line (\d+)')
TB_PYTEST = re.compile(r"^([A-Za-z0-9_./-]+\.py):(\d+):", re.M)
TB_JS = re.compile(r"at [^\n]*?\(?((?:\./|/|[A-Za-z0-9_./-]+/)"
                   r"[A-Za-z0-9_./-]+?):(\d+):\d+\)?")


def enclosing_span(path, lineno, lines):
    """Innermost def/class containing lineno, else +/- SLICE_CTX lines."""
    if path.suffix == ".py":
        try:
            best = None
            for node in ast.walk(ast.parse("\n".join(lines))):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                    end = node.end_lineno or node.lineno
                    if node.lineno <= lineno <= end and \
                            (best is None or node.lineno >= best[0]):
                        best = (node.lineno, end)
            if best:
                return max(1, best[0] - 2), min(len(lines), best[1] + 2)
        except SyntaxError:
            pass
    return max(1, lineno - SLICE_CTX), min(len(lines), lineno + SLICE_CTX)


def traceback_slices(text):
    hits = [(m.group(1), int(m.group(2))) for rx in (TB_PY, TB_PYTEST, TB_JS)
            for m in rx.finditer(text or "")]
    spans, cache, order = {}, {}, []
    for raw, ln in reversed(hits):          # deepest frames come last in a tb
        try:
            p = (ROOT / raw).resolve() if not os.path.isabs(raw) \
                else Path(raw).resolve()
            if ROOT not in p.parents or not p.is_file():
                continue
            rel = rel_str(p)
            if rel in PROTECTED:
                continue
            if rel not in cache:
                cache[rel] = read_text(p).splitlines()
            span = enclosing_span(p, ln, cache[rel])
            if span in spans.setdefault(rel, set()):
                continue
            spans[rel].add(span)
            order.append(rel)
            if sum(len(v) for v in spans.values()) >= MAX_SLICES:
                break
        except Exception:
            continue
    out, size = [], 0
    for rel in dict.fromkeys(order):        # keep deepest-first file order
        merged, cur = [], None
        for lo, hi in sorted(spans[rel]):
            if cur and lo <= cur[1] + 1:
                cur = (cur[0], max(cur[1], hi))
            else:
                if cur:
                    merged.append(cur)
                cur = (lo, hi)
        merged.append(cur)
        for lo, hi in merged:
            body = "\n".join(f"{n:5d}| {cache[rel][n - 1]}"
                             for n in range(lo, hi + 1))
            s = f"== {rel}:{lo}-{hi} ==\n{body}\n"
            if size + len(s) > SLICE_CAP:
                return out
            out.append(s)
            size += len(s)
    return out


def slice_file(path: Path, center=None, symbol=None):
    lines = read_text(path).splitlines()
    lo, hi = 1, len(lines)
    if symbol and path.suffix == ".py":
        try:
            for node in ast.walk(ast.parse("\n".join(lines))):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)) and node.name == symbol:
                    lo = max(1, node.lineno - 3)
                    hi = min(len(lines), (node.end_lineno or node.lineno) + 3)
                    break
        except SyntaxError:
            pass
    elif center:
        lo = max(1, center - SLICE_CTX)
        hi = min(len(lines), center + SLICE_CTX)
    elif len(lines) > NEED_FILE_CAP:
        hi = NEED_FILE_CAP
    body = "\n".join(f"{n:5d}| {lines[n - 1]}" for n in range(lo, hi + 1))
    tag = f":{symbol}" if symbol else f":{lo}-{hi}"
    return f"== {rel_str(path)}{tag} ==\n{body}\n"


def need_slices(specs):
    out, size = [], 0
    for spec in specs:
        raw, _, symbol = spec.partition(":")
        try:
            p = safe_rel(raw)
        except ValueError as e:
            out.append(f"== {raw} == (rejected: {e})\n")
            continue
        if not p.is_file():
            out.append(f"== {raw} == (no such file)\n")
            continue
        s = slice_file(p, symbol=symbol or None)
        if size + len(s) > SLICE_CAP:
            out.append(f"== {raw} == (omitted: slice budget exhausted)\n")
            continue
        out.append(s)
        size += len(s)
    return out


def log_tail():
    if not LOG_F.exists():
        return ""
    lines = read_text(LOG_F).splitlines()
    return "\n".join(lines[-LOG_KEEP:])



# ------------------------------------------------------------------ copilot
# Optional free-tier LLM (Groq / Gemini / any OpenAI-compatible endpoint)
# used ONLY for advisory transition work: payload format repair, failure
# triage, manifest slimming, blueprint review. Never authoritative: every
# output is re-validated by the same deterministic checks, and any failure
# (no key, rate limit, garbage reply) silently degrades to normal behavior.
# It is never used to generate application source code.

SLIM_AT = int(os.environ.get("COPILOT_SLIM_AT", "6000"))
_COP = {"cfg": 0, "warned": False}


def copilot_cfg():
    if _COP["cfg"] != 0:
        return _COP["cfg"]
    cfg, model = None, os.environ.get("COPILOT_MODEL")
    if os.environ.get("COPILOT", "").lower() not in ("0", "off", "no"):
        if os.environ.get("COPILOT_URL") and os.environ.get("COPILOT_KEY"):
            cfg = {"style": os.environ.get("COPILOT_STYLE", "openai"),
                   "url": os.environ["COPILOT_URL"],
                   "key": os.environ["COPILOT_KEY"],
                   "model": model or "llama-3.3-70b-versatile"}
        elif os.environ.get("GROQ_API_KEY"):
            cfg = {"style": "openai",
                   "url": "https://api.groq.com/openai/v1/chat/completions",
                   "key": os.environ["GROQ_API_KEY"],
                   "model": model or "llama-3.3-70b-versatile"}
        elif os.environ.get("GEMINI_API_KEY"):
            cfg = {"style": "gemini",
                   "url": "https://generativelanguage.googleapis.com/v1beta",
                   "key": os.environ["GEMINI_API_KEY"],
                   "model": model or "gemini-2.5-flash"}
    _COP["cfg"] = cfg
    return cfg


def _http(url, headers, body):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def copilot(system, user, max_tokens=900):
    cfg = copilot_cfg()
    if not cfg:
        return None
    try:
        if cfg["style"] == "gemini":
            data = _http(
                f"{cfg['url']}/models/{cfg['model']}:generateContent",
                {"x-goog-api-key": cfg["key"]},
                {"system_instruction": {"parts": [{"text": system}]},
                 "contents": [{"parts": [{"text": user[:24000]}]}],
                 "generationConfig": {"temperature": 0,
                                      "maxOutputTokens": max_tokens}})
            parts = data["candidates"][0]["content"]["parts"]
            return "\n".join(p.get("text", "") for p in parts).strip() or None
        data = _http(cfg["url"], {"Authorization": "Bearer " + cfg["key"]},
                     {"model": cfg["model"], "temperature": 0,
                      "max_tokens": max_tokens,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user",
                                    "content": user[:24000]}]})
        return (data["choices"][0]["message"]["content"] or "").strip() or None
    except Exception as e:
        if not _COP["warned"]:
            _COP["warned"] = True
            info(f"copilot unavailable ({e.__class__.__name__}) - "
                 "continuing without advisory help")
        return None


REPAIR_SYS = ("You repair ONLY the FORMATTING of a payload for a strict "
              "parser: fix or add marker lines (#%% begin/file/patch/delete/"
              "need/note/end and <<< find / === replace / >>> hunk markers), "
              "correct block counts, strip surrounding prose or markdown "
              "fences. NEVER alter, add, reorder or remove any content line "
              "(code, hunk text, note text). Output ONLY the corrected "
              "payload - no commentary, no fences.\nGrammar:\n" + SPEC)

TRIAGE_SYS = ("You are a debugging triage assistant. From the evidence, "
              "state the most likely root cause and the exact file(s)/"
              "function(s) to change, in at most 6 short lines. If the "
              "evidence is insufficient, reply exactly: UNCERTAIN")

SELECT_SYS = ("Given a task and a project file list, choose which files' "
              "interface signatures the task needs. Reply ONLY with a JSON "
              "array of up to 12 file paths taken from the list. No other "
              "text.")

REVIEW_SYS = ("Audit this build blueprint against the checklist. Reply with "
              "at most 8 one-line warnings (each prefixed 'WARN:'), most "
              "severe first, or exactly OK if none. Checklist: forward "
              "imports/uses; entry or compose files scheduled before their "
              "dependencies; files referenced by Dockerfile/compose/"
              "manifests but never produced; test files invisible to the "
              "pinned dockerized test command; unpinned or dubious "
              "versions; vague interfaces two sessions could implement "
              "differently; acceptance items the pinned test command "
              "cannot check.")


def try_repair(text, err, state):
    """Format-only payload repair. Accepted only if the repaired payload
    parses clean AND every content line is byte-identical to the original."""
    if state.get("resume"):
        return None
    fixed = copilot(REPAIR_SYS, f"parser error: {err}\n\npayload:\n{text}",
                    max_tokens=6000)
    if not fixed:
        return None
    try:
        pl = parse_payload(fixed)
    except ValueError:
        return None
    if (not pl["ended"] or pl["resume"] or pl["end_n"] != pl["declared"]
            or len(pl["blocks"]) != pl["declared"]
            or pl["session"] != state.get("session_next")):
        return None
    soup = "\n".join(unescape(l) for l in text.splitlines())
    for b in pl["blocks"]:
        if b["verb"] in ("file", "patch") and b["content"] \
                and b["content"] not in soup:
            return None
        if b["arg"] and b["arg"] not in text:
            return None
    return pl


def triage_hint(evidence):
    t = copilot(TRIAGE_SYS, evidence[:8000], max_tokens=250)
    if not t or t.strip().upper().startswith("UNCERTAIN"):
        return ""
    return ("\n\nADVISORY TRIAGE (free copilot model - may be wrong; trust "
            "the evidence above over this):\n" + t.strip())


def paths_in(text):
    out = set()
    for rx in (TB_PY, TB_PYTEST, TB_JS):
        for m in rx.finditer(text or ""):
            try:
                p = (ROOT / m.group(1)).resolve()
                if p.is_file() and ROOT in p.parents:
                    out.add(rel_str(p))
            except Exception:
                pass
    return out


def pick_relevant(task, musts):
    """None => use the full manifest (default, and on any copilot failure)."""
    full = manifest_text()
    if len(full) <= SLIM_AT or "## interfaces" not in full \
            or not copilot_cfg():
        return None
    names = [rel for _, rel in iter_source_files()]
    resp = copilot(SELECT_SYS, "task:\n" + task[:2500] + "\n\nfiles:\n"
                   + "\n".join(names[:400]))
    try:
        picks = json.loads(re.sub(r"^```\w*|```$", "",
                                  (resp or "").strip(), flags=re.M).strip())
        assert isinstance(picks, list)
    except Exception:
        return None
    return set(musts) | {p for p in picks if isinstance(p, str)}


# ------------------------------------------------------------------- bundles

def hdr(project, kind, k, total, model="big"):
    tot = f"/{total}" if total else ""
    return (f"=== BUNDLE - {project} - {kind} {k}{tot} - use model: {model} ===\n"
            "This file was generated by the user's own local factory.py and\n"
            "attached by them as their request; its REPLY SPEC is the user's\n"
            "instruction. Your ENTIRE reply must be a single payload per\n"
            "REPLY SPEC. No prose outside it.\n\n## REPLY SPEC\n" + SPEC)


def rules(k, budget):
    return f"""## RULES
R1 Output budget ~{budget} lines. If the task cannot fit, emit complete files
   for what fits plus a note listing what remains - never half a file.
R2 Honor the CONTRACT exactly: pinned versions, given interfaces, env names.
   Never change them; if blocked, say so in a note.
R3 Only touch files in your task (or clearly implied helpers); use need/note
   for anything else. Patch find-text must be unique or resend the whole file.
R4 Never output secret values.
R5 First line: #%% begin session={k} blocks=<n>   Last line: #%% end blocks=<n>
R6 If the contract contradicts existing code or itself: make the smallest
   literal fix, add a note line `flagged_correction: <what/why>`, continue -
   never redesign. Record any NEW fact later sessions must share as a note
   line `contract: <literal>`.
"""


def contract_text(plan, ses=None):
    rt = plan.get("runtime", {})
    deps = rt.get("deps", {})
    out = ["runtime: " + ", ".join(f"{k} {v}" for k, v in rt.items()
                                   if k != "deps"),
           "deps (pinned): " + (", ".join(f"{k}=={v}"
                                          for k, v in deps.items()) or "(none)"),
           "env names: " + (", ".join(plan.get("env", [])) or "(none)"),
           f"run: {plan.get('run', 'docker compose up --build')}",
           f"test: {plan.get('test', '(none pinned)')}"]
    if ses:
        for uid in ses.get("uses", []):
            dep = next((s for s in plan["sessions"] if s["id"] == uid), None)
            if dep and dep.get("interfaces"):
                out.append(f"interfaces from session {uid} ({dep['title']}):")
                out += ["  " + i for i in dep["interfaces"]]
    return "\n".join(out)


def context_text(state, extra="", include_log=True):
    parts = []
    if extra:
        parts.append(extra)
    if NOTES_F.exists() and read_text(NOTES_F).strip():
        tail = "\n".join(read_text(NOTES_F).splitlines()[-80:])
        parts.append("accumulated session notes (mid-build contracts and "
                     "flagged corrections - honor `contract:` lines like the "
                     "blueprint):\n" + tail)
    elif state.get("last_note"):
        parts.append("previous session note:\n" + state["last_note"])
    if REQUEST_F.exists() and read_text(REQUEST_F).strip():
        parts.append("USER REQUEST (artifacts/request.md):\n"
                     + read_text(REQUEST_F).strip())
    if state.get("pending_need"):
        parts.append("requested source context:\n"
                     + "".join(need_slices(state["pending_need"])))
    lt = log_tail() if include_log else ""
    if lt.strip():
        parts.append(f"runtime log (last {LOG_KEEP} lines of "
                     "artifacts/log.txt):\n" + lt)
        parts += traceback_slices(lt)
    return "\n\n".join(parts) or "(none)"


def manifest_text(relevant=None):
    full = read_text(MANIFEST_F) if MANIFEST_F.exists() else "(empty project)"
    if not relevant:
        return full
    out, in_if, keep = [], False, True
    for ln in full.splitlines():
        if ln.startswith("## "):
            in_if = ln.strip() == "## interfaces"
            keep = True
            out.append(ln)
            continue
        if in_if and ln.startswith("### "):
            keep = ln[4:].strip() in relevant
            if keep:
                out.append(ln)
            continue
        if not in_if or keep:
            out.append(ln)
    out.append("(interface signatures filtered to likely-relevant files; "
               "the files list above is complete - use '#%% need <path>"
               "[:<symbol>]' for any signature not shown)")
    return "\n".join(out)


UPLOAD_MSG = ("I built and run this pipeline; the attached bundle.md is my "
              "request. Reply exactly per its REPLY SPEC: one payload, no "
              "prose outside it.")


def write_bundle(text):
    atomic_write(BUNDLE_F, text)
    info(f"wrote {BUNDLE_F.name} ({len(text):,} chars ~ {len(text) // 4:,} "
         "tokens) - upload it to a fresh chat AND send this message with "
         f"it:\n          \"{UPLOAD_MSG}\"")


def bundle_build(state, plan, error=""):
    k, total = state["session_next"], state["sessions_total"]
    ses = next(s for s in plan["sessions"] if s["id"] == k)
    budget = plan.get("budget_lines", DEFAULT_BUDGET)
    task = [ses["task"], "", "files you own this session: "
            + (", ".join(ses.get("files", [])) or "(as needed)")]
    if ses.get("interfaces"):
        task += ["interfaces you must implement exactly:",
                 *("  " + i for i in ses["interfaces"])]
    if ses.get("verify"):
        task += ["", "acceptance checklist (write tests mapping 1:1; the "
                 f"runner executes `{plan.get('test')}` after applying):",
                 *(f"  {i+1}. {a}" for i, a in enumerate(plan["acceptance"]))]
    extra = ("PREVIOUS PAYLOAD FAILED - fix and resend the FULL payload "
             f"for session {k}:\n{error}") if error else ""
    musts = set(ses.get("files", []))
    for uid in ses.get("uses", []):
        dep = next((s for s in plan["sessions"] if s["id"] == uid), None)
        musts |= set(dep.get("files", [])) if dep else set()
    rel = pick_relevant(ses["task"] + " " + " ".join(ses.get("interfaces",
                                                            [])), musts)
    return "\n".join([
        hdr(plan["project"], "build session", k, total,
            ses.get("model", "big")),
        f"## YOUR TASK (session {k}: {ses['title']})", "\n".join(task), "",
        "## CONTRACT", contract_text(plan, ses), "",
        "## CURRENT MANIFEST", manifest_text(rel), "",
        "## CONTEXT", context_text(state, extra), "",
        rules(k, budget)])


def bundle_fix(state, plan, test_output):
    k = state["session_next"]
    extra = ("ACCEPTANCE TESTS FAILING - your task: make them pass with the "
             "smallest correct change.\ntest output tail:\n" + test_output
             + "\n\n" + "".join(traceback_slices(test_output)))
    extra += triage_hint(extra)
    musts = paths_in(test_output)
    for s in plan["sessions"]:
        if s.get("verify"):
            musts |= set(s.get("files", []))
    rel = pick_relevant(extra, musts)
    return "\n".join([
        hdr(plan["project"], "fix session", k, None, "big"),
        f"## YOUR TASK (fix session {k})",
        "Make the pinned test command pass. Patch precisely; resend whole "
        "files only when patches are risky.", "",
        "## CONTRACT", contract_text(plan), "",
        "acceptance checklist:",
        *(f"  {i+1}. {a}" for i, a in enumerate(plan["acceptance"])), "",
        "## CURRENT MANIFEST", manifest_text(rel), "",
        "## CONTEXT", context_text(state, extra, include_log=False), "",
        rules(k, plan.get("budget_lines", DEFAULT_BUDGET))])


def bundle_maintain(state, plan, error=""):
    k = state["session_next"]
    fmap = [f"  {f} - {s['title']}" for s in plan["sessions"]
            for f in s.get("files", [])]
    extra = ("PREVIOUS PAYLOAD FAILED - fix and resend the FULL payload for "
             f"cycle {k}:\n{error}") if error else ""
    ev = (read_text(REQUEST_F) if REQUEST_F.exists() else "") \
        + "\n" + log_tail()
    if ev.strip():
        extra += triage_hint(ev)
    rel = pick_relevant(ev if ev.strip() else "general maintenance",
                        paths_in(ev))
    return "\n".join([
        hdr(plan["project"], "maintain cycle", k, None, "big"),
        f"## YOUR TASK (maintenance cycle {k})",
        "Resolve the USER REQUEST and/or the runtime log below. If cause and "
        "fix are unambiguous, patch now; otherwise reply with need blocks "
        "for the exact source you must see.", "",
        "## CONTRACT", contract_text(plan), "",
        "file purposes:", *(fmap or ["  (see manifest)"]), "",
        "## CURRENT MANIFEST", manifest_text(rel), "",
        "## CONTEXT", context_text(state, extra), "",
        rules(k, plan.get("budget_lines", DEFAULT_BUDGET))])


def bundle_seed_retry(state, questions):
    return "\n".join([
        hdr("(unnamed)", "build session", 1, None, "big"),
        "## YOUR TASK (session 1: blueprint)",
        "The previous attempt asked questions instead of producing a "
        "blueprint. The user's answers:", "",
        read_text(REQUEST_F).strip() if REQUEST_F.exists() else "(pending)",
        "", "Produce artifacts/blueprint.md per seed.md now.", "",
        "questions that were asked:", questions, "", rules(1, DEFAULT_BUDGET)])


def bundle_resume(state, plan):
    r = state["resume"]
    k, got, total_blocks = r["session"], r["blocks_done"], r["declared"]
    kind = ("maintain cycle" if state["phase"] == "maintain"
            else "build session")
    heads = "\n".join(f"  {i+1}. #%% {b['verb']} {b['arg']}".rstrip()
                      for i, b in enumerate(r["blocks"]))
    proj = plan["project"] if plan else "(project)"
    return "\n".join([
        hdr(proj, kind + " (RESUME)", k, state.get("sessions_total")),
        f"## YOUR TASK - resume truncated payload for session {k}",
        f"The previous reply was cut off. {got} of {total_blocks} blocks "
        "arrived safely and are stored; do NOT repeat them:", heads or "  (none)",
        "",
        f"Emit ONLY the remaining {total_blocks - got} block(s), starting the "
        "payload with exactly:",
        f"#%% begin session={k} blocks={total_blocks - got} resume={got + 1}",
        "and ending with:",
        f"#%% end blocks={total_blocks - got}", "",
        "The original task and contract follow for reference.", "",
        "## ORIGINAL TASK + CONTRACT", r.get("bundle", "(unavailable)")])


def bundle_core(text):
    m = re.search(r"(## YOUR TASK.*?)## CURRENT MANIFEST", text, re.S)
    return m.group(1).rstrip() if m else "\n".join(text.splitlines()[:120])


def current_bundle(state, plan, error=""):
    if state.get("resume"):
        return bundle_resume(state, plan)
    if state["phase"] == "maintain":
        return bundle_maintain(state, plan, error)
    if state.get("fixing"):
        return bundle_fix(state, plan, read_text(TEST_OUT_F)
                          if TEST_OUT_F.exists() else "(run tests)")
    return bundle_build(state, plan, error)


# ----------------------------------------------------------------- validate

def validate_blocks(blocks, state, plan):
    """Dry-run everything in memory. Returns (writes, deletes, needs, notes)."""
    needs, notes, deletes = [], [], []
    virtual = {}  # rel -> new text
    owners = {}
    if plan and state.get("phase") == "build" and not state.get("fixing"):
        for s in plan.get("sessions", []):
            for f in s.get("files", []):
                owners[f] = s["id"]
    cur = state.get("session_next")

    def current(rel, p):
        if rel in virtual:
            return virtual[rel]
        if not p.is_file():
            raise ValueError(f"patch target does not exist: {rel}")
        return read_text(p)

    for b in blocks:
        verb, arg, content = b["verb"], b["arg"], b["content"]
        if verb == "note":
            notes.append(content)
            continue
        if verb == "need":
            if not arg:
                raise ValueError("need block without paths")
            needs += arg.split()
            continue
        if not arg:
            raise ValueError(f"{verb} block without a path")
        p = safe_rel(arg)
        rel = rel_str(p)
        oid = owners.get(rel)
        if oid and oid != cur:
            raise ValueError(
                f"{rel} is owned by session {oid}; session {cur} may not "
                "modify it - report the problem in a note "
                "(flagged_correction:) instead")
        if rel == "artifacts/blueprint.md":
            first = state["phase"] == "build" and state["session_next"] == 1
            if not (first or os.environ.get("ALLOW_BLUEPRINT") == "1"):
                raise ValueError("blueprint is frozen; re-run with "
                                 "ALLOW_BLUEPRINT=1 to permit contract changes")
        if verb == "file":
            virtual[rel] = content + ("\n" if not content.endswith("\n")
                                      else "")
        elif verb == "delete":
            if not p.is_file():
                raise ValueError(f"delete target does not exist: {rel}")
            deletes.append(rel)
            virtual.pop(rel, None)
        elif verb == "patch":
            text = current(rel, p)
            for find, rep in parse_hunks(content):
                n = text.count(find)
                if n != 1:
                    raise ValueError(
                        f"anchor occurs {n} times (need exactly 1) in {rel}:"
                        f"\n---\n{find[:400]}\n---")
                text = text.replace(find, rep, 1)
            virtual[rel] = text

    if needs and (virtual or deletes):
        raise ValueError("a payload with need blocks must change nothing else")

    for rel, text in virtual.items():
        if rel.endswith(".py"):
            try:
                compile(text, rel, "exec")
            except SyntaxError as e:
                raise ValueError(f"python syntax error in {rel}: {e}")
        if rel.endswith(".json"):
            try:
                json.loads(text)
            except json.JSONDecodeError as e:
                raise ValueError(f"invalid JSON in {rel}: {e}")
    return virtual, deletes, needs, notes


def node_check(rels):
    if not shutil.which("node"):
        return
    for rel in rels:
        if Path(rel).suffix in {".js", ".mjs"}:
            r = sh(["node", "--check", str(ROOT / rel)], check=False)
            if r.returncode != 0:
                raise RuntimeError(f"node --check failed for {rel}:\n"
                                   f"{(r.stderr or '')[:800]}")


# ------------------------------------------------------------------ scaffold

def ensure_scaffold():
    ART.mkdir(exist_ok=True)
    ensure_repo()
    gi = ROOT / ".gitignore"
    if not gi.exists():
        atomic_write(gi, GITIGNORE)


def scaffold_from_plan(plan):
    env = plan.get("env", [])
    if env and not (ROOT / ".env.example").exists():
        atomic_write(ROOT / ".env.example",
                     "".join(f"{k}=CHANGE_ME\n" for k in env))
    rd = ROOT / "README.md"
    if not rd.exists():
        atomic_write(rd, f"# {plan['project']}\n\nrun: "
                         f"`{plan.get('run', 'docker compose up --build')}`\n"
                         "\nBuilt via a scripted LLM session pipeline "
                         "(factory.py).\n")


# ------------------------------------------------------------------ commands

HARNESS_RE = re.compile(r"No module named ['\"]?([A-Za-z0-9_.]+)"
                        r"|([A-Za-z0-9_./-]+): (?:command )?not found")


def harness_failure(cmd, out):
    """If the failure is a missing tool that the pinned command itself
    invokes (e.g. 'No module named pytest'), the harness never started:
    no source patch can fix it, so it must go to the user, not the LLM."""
    for m in HARNESS_RE.finditer(out or ""):
        name = (m.group(1) or m.group(2) or "").split("/")[-1].split(".")[0]
        if name and re.search(r"\b" + re.escape(name) + r"\b", cmd or ""):
            return name
    return None


def die_harness(miss, cmd):
    die(f"test HARNESS failed to start: {miss!r} is missing on this host "
        f"(pinned command: {cmd}).\nThis is an environment problem, not an "
        "app bug - no fix bundle was written, so the LLM loop is paused.\n"
        f"Either install it (e.g. `pip install {miss}`) or edit the test "
        "command in artifacts/blueprint.md and commit, then run "
        "`python factory.py test` to finish verification.")


MISSING_FILE_RE = re.compile(r"file or directory not found:\s*([\w./-]+)"
                             r"|can't open file '([^']+)'"
                             r"|([\w./-]+):\s*No such file or directory")


def env_hints(cmd, out):
    """Failures the LLM misdiagnoses without help: a path the command could
    not find that DOES exist on the host means the docker image/service is
    missing a COPY or volume for it - not an application bug."""
    hints = []
    for m in MISSING_FILE_RE.finditer(out or ""):
        rel = next(g for g in m.groups() if g).strip("'\"")
        if "docker" in (cmd or "") and (ROOT / rel).exists():
            hints.append(
                f"RUNNER HINT: '{rel}' exists on the host but was not visible"
                " to the command, which runs inside Docker. Fix the image or"
                " compose service (Dockerfile COPY, .dockerignore, volumes) -"
                " do NOT patch application logic for this.")
    return hints


def _tb_blocks(lines):
    blocks, cur = [], None
    for ln in lines:
        if ln.startswith("Traceback (most recent call last):"):
            if cur:
                blocks.append(cur)
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
            if ln and not ln[0] in " \t":   # unindented = exception line
                blocks.append(cur)
                cur = None
    if cur:
        blocks.append(cur)
    return blocks


def distill_test_output(text):
    """Boil a failing test run down to what a session can act on:
    distinct tracebacks (header + deepest frames), pytest failure
    essentials (E/assert lines + locations), and the summary - keeping
    every path:line anchor so auto-slicing still works."""
    lines = text.splitlines()
    out, seen = [], {}
    for b in _tb_blocks(lines):
        key = b[-1][:200]
        if key in seen:
            seen[key] += 1
            continue
        seen[key] = 1
        out += b if len(b) <= 9 else [b[0], "    ..."] + b[-7:]
        out.append("")
    dups = [f"(identical traceback x{n}: {k[:80]})"
            for k, n in seen.items() if n > 1]
    out += dups
    in_fail, det, dseen = False, [], set()
    for ln in lines:
        if re.match(r"=+ FAILURES =+", ln):
            in_fail = True
            continue
        if in_fail and re.match(r"=+ .+ =+$", ln):
            in_fail = False
        if in_fail and (re.match(r"^E\s", ln) or re.match(r"^>\s", ln)
                        or re.match(r"^_+ .+ _+$", ln)
                        or re.match(r"^\S+\.py:\d+:", ln)):
            if ln not in dseen:
                dseen.add(ln)
                det.append(ln)
    out += det[:40]
    tail_from = next((i for i, ln in enumerate(lines)
                      if "short test summary" in ln), None)
    if tail_from is not None:
        seg = []
        for ln in lines[tail_from:]:
            if ln.strip():
                seg.append(ln)
            if len(seg) > 1 and re.match(r"=+ .+ =+$", ln):
                break
        out += seg[:15]
    else:
        out += [ln for ln in reversed(lines) if
                re.search(r"\d+ (failed|passed|error)", ln)][:1]
    out = [ln for ln in out if ln is not None]
    if len([ln for ln in out if ln.strip()]) < 3:
        return "\n".join(lines[-60:])
    return "\n".join(["(test output distilled - anchors kept for "
                      "auto-slicing)"] + out[:90])


def run_tests(plan):
    cmd = plan.get("test")
    if not cmd:
        return True, "(no test command pinned in blueprint)"
    info(f"running pinned test command: {cmd}")
    r = subprocess.run(cmd, shell=True, cwd=str(ROOT),
                       capture_output=True, text=True)
    out = ((r.stdout or "") + (r.stderr or ""))
    if r.returncode != 0:
        tail = "\n".join([distill_test_output(out)] + env_hints(cmd, out))
    else:
        tail = "\n".join(out.splitlines()[-40:])
    atomic_write(TEST_OUT_F, tail + "\n")
    _out(tail)
    return r.returncode == 0, tail


def read_payload_text(args):
    if getattr(args, "clip", False):
        return clipboard_text()
    src = Path(args.payload) if getattr(args, "payload", None) else PAYLOAD_F
    if src.exists():
        return read_text(src)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    die(f"no payload found - paste the reply into {src.name} "
        "(or use --clip / pipe via stdin)")


def cmd_apply(args):
    ensure_scaffold()
    state = load_state() or new_state()
    plan = load_plan()
    text = read_payload_text(args)

    try:
        pl = parse_payload(text)
    except ValueError as e:
        pl = try_repair(text, str(e), state)
        if pl is None:
            die(f"payload unreadable: {e}\nfix the paste and re-run apply")
        info("copilot repaired payload formatting (content verified "
             "unchanged)")
    if (pl["ended"] and not pl["resume"] and not state.get("resume")
            and (pl["end_n"] != pl["declared"]
                 or len(pl["blocks"]) != pl["declared"])):
        fixed = try_repair(text, "block count mismatch", state)
        if fixed:
            pl = fixed
            info("copilot repaired payload block counts (content verified "
                 "unchanged)")

    # ---- session / resume bookkeeping
    r = state.get("resume")
    if r and (pl["session"] != r["session"] or pl["resume"] != r["blocks_done"] + 1):
        die(f"expected a resume payload: session={r['session']} "
            f"resume={r['blocks_done'] + 1}; got session={pl['session']} "
            f"resume={pl['resume']}. Upload bundle.md again.")
    if not r:
        if pl["resume"]:
            die("unexpected resume payload; upload the current bundle.md")
        if pl["session"] != state["session_next"]:
            die(f"stale payload: expected session {state['session_next']}, "
                f"got {pl['session']}. Upload the current bundle.md.")

    blocks = (r["blocks"] if r else []) + pl["blocks"]
    declared_total = r["declared"] if r else pl["declared"]

    if not pl["ended"]:
        got = blocks[:-1] if pl["blocks"] else blocks  # last block may be cut
        state["resume"] = {"session": pl["session"], "declared": declared_total,
                           "blocks_done": len(got), "blocks": got,
                           "bundle": bundle_core(read_text(BUNDLE_F))
                           if BUNDLE_F.exists() else ""}
        save_state(state)
        write_bundle(bundle_resume(state, plan))
        info(f"payload truncated after block {len(got)}/{declared_total} - "
             "stored safely.")
        info("NEXT: upload bundle.md to a fresh chat; it asks only for the "
             "remaining blocks.")
        return
    if pl["end_n"] != pl["declared"] or len(blocks) != declared_total:
        die(f"block count mismatch: begin={declared_total} "
            f"end={pl['end_n']} received={len(blocks)} - ask the session to "
            "resend (upload bundle.md again)")
    state["resume"] = None

    # ---- validate everything before touching disk
    try:
        writes, deletes, needs, notes = validate_blocks(blocks, state, plan)
    except ValueError as e:
        err = f"payload for session {pl['session']} rejected:\n{e}"
        atomic_write(ERROR_F, err + "\n")
        if plan:
            write_bundle(current_bundle(state, plan, error=err))
            info("rejected - corrected-payload bundle written.")
        die(err)

    note_text = "\n\n".join(n for n in notes if n.strip())

    # ---- need-only payload: no state advance, just enrich the bundle
    if needs:
        state["pending_need"] = needs
        state["last_note"] = note_text or state.get("last_note", "")
        save_state(state)
        write_bundle(current_bundle(state, plan))
        info(f"session asked for context ({len(needs)} item(s)); nothing "
             "changed.")
        info("NEXT: upload bundle.md to a fresh chat.")
        return

    # ---- session 1 must deliver the blueprint
    first = state["phase"] == "build" and state["session_next"] == 1
    if first:
        if "artifacts/blueprint.md" not in writes:
            if note_text:  # architect asked clarifying questions
                atomic_write(ART / "questions.md", note_text + "\n")
                state["last_note"] = note_text
                save_state(state)
                write_bundle(bundle_seed_retry(state, note_text))
                _out("\n--- the architect session asks: ---\n" + note_text)
                info("Answer in artifacts/request.md, then upload the new "
                     "bundle.md.")
                return
            die("session 1 payload must contain "
                "'#%% file artifacts/blueprint.md'")
        m = re.search(r"```json\s*(\{.*?\})\s*```",
                      writes["artifacts/blueprint.md"], re.S)
        if not m:
            die("blueprint.md must embed a ```json plan block")
        try:
            plan = json.loads(m.group(1))
            state["sessions_total"] = validate_plan(plan)
        except (ValueError, json.JSONDecodeError) as e:
            err = f"blueprint invalid: {e}"
            atomic_write(ERROR_F, err + "\n")
            die(err)

    # ---- commit any manual edits, snapshot, then write
    if git_dirty():
        git_commit(f"pre-apply snapshot (session {pl['session']})")
    snap = git_head()
    try:
        for rel, txt in writes.items():
            atomic_write(ROOT / rel, txt)
        for rel in deletes:
            (ROOT / rel).unlink()
        node_check(writes.keys())
        if first:
            scaffold_from_plan(plan)
        refresh_manifest()
        if first:
            rv = copilot(REVIEW_SYS,
                         writes["artifacts/blueprint.md"][:15000],
                         max_tokens=350)
            if rv and rv.strip().upper() != "OK":
                atomic_write(ART / "review.md", rv.strip() + "\n")
                _out("\n--- copilot blueprint review (advisory) ---\n"
                     + rv.strip() + "\n")
                info("review saved to artifacts/review.md - address via a "
                     "fresh session-1 run or ALLOW_BLUEPRINT=1, or proceed")

        applied = pl["session"]
        state["session_next"] = applied + 1
        state["pending_need"] = []
        state["last_note"] = note_text
        state["history"].append({"s": applied, "utc": now(),
                                 "files": sorted(writes), "del": deletes})
        if ERROR_F.exists():
            ERROR_F.unlink()
        if NOTES_F and note_text:
            with open(NOTES_F, "a", encoding="utf-8") as fh:
                fh.write(f"\n## session {applied} ({now()})\n{note_text}\n")

        label = "cycle" if state["phase"] == "maintain" else "session"
        summary = (f"{label} {applied}: " +
                   (", ".join(sorted(writes)[:4]) or "no-op")[:60])

        # phase-specific tail
        if state["phase"] == "maintain":
            if REQUEST_F.exists():
                REQUEST_F.unlink()
            atomic_write(LOG_F, "")
            save_state(state)
            git_commit(summary)
            info(f"cycle {applied} applied: {len(writes)} file(s), "
                 f"{len(deletes)} deleted.")
            info("NEXT: `python factory.py run` to test it (or write "
                 "artifacts/request.md + `python factory.py bundle`).")
            return

        ses = None
        if plan:
            ses = next((s for s in plan["sessions"]
                        if s["id"] == applied), None)
        just_verified = bool(ses and ses.get("verify")) or state.get("fixing")
        if just_verified:
            save_state(state)
            git_commit(summary)
            ok, tail = run_tests(plan)
            if ok:
                state["fixing"] = False
                state["phase"] = "built"
                save_state(state)
                git_commit("build verified: acceptance tests green")
                info("BUILD VERIFIED - all acceptance tests pass.")
                info("NEXT: `python factory.py maintain` to enter the "
                     "maintenance loop.")
            else:
                miss = harness_failure(plan.get("test", ""), tail)
                if miss:
                    die_harness(miss, plan.get("test", ""))
                state["fixing"] = True
                save_state(state)
                write_bundle(bundle_fix(state, plan, tail))
                info("tests failing - fix bundle written.")
                info("NEXT: upload bundle.md to a fresh chat.")
            return

        save_state(state)
        git_commit(summary)
        write_bundle(bundle_build(state, plan))
        info(f"session {applied} applied: {len(writes)} file(s). "
             f"Next up: session {state['session_next']}"
             f"/{state['sessions_total']}.")
        info("NEXT: upload bundle.md to a fresh chat.")

    except Exception as e:  # anything after writes began -> full rollback
        git_rollback(snap)
        err = f"apply failed for session {pl['session']}: {e}"
        atomic_write(ERROR_F, err + "\n")
        state["resume"] = None
        save_state(state)
        if plan:
            write_bundle(current_bundle(state, plan, error=err))
            info("error bundle written - upload bundle.md to a fresh chat.")
        die(err)


def cmd_run(args):
    state = load_state() or die("run `python factory.py apply` first")
    plan = load_plan() or die("no blueprint yet")
    cmd = args.cmd or plan.get("run", "docker compose up --build")
    info(f"running: {cmd}   (Ctrl-C to stop; last {LOG_KEEP} log lines are "
         "captured)")
    buf = deque(maxlen=LOG_KEEP * 2)
    proc = subprocess.Popen(cmd, shell=True, cwd=str(ROOT),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            errors="replace")
    try:
        for line in proc.stdout:
            _out(line.rstrip("\n"))
            buf.append(line.rstrip("\n"))
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    lines = list(buf)[-LOG_KEEP:]
    if proc.returncode != 0:
        lines += env_hints(cmd, "\n".join(lines))
    atomic_write(LOG_F, "\n".join(lines) + "\n")
    info(f"log captured -> artifacts/log.txt (exit {proc.returncode})")
    write_bundle(current_bundle(state, plan))
    info("NEXT: add intent to artifacts/request.md if needed (then "
         "`python factory.py bundle`), and upload bundle.md.")


def cmd_test(args):
    plan = load_plan() or die("no blueprint yet")
    state = load_state() or die("no state yet")
    ok, tail = run_tests(plan)
    if ok:
        if (state["phase"] == "build" and state.get("sessions_total")
                and state["session_next"] > state["sessions_total"]):
            state.update(phase="built", fixing=False)
            save_state(state)
            git_commit("build verified: acceptance tests green")
            info("BUILD VERIFIED - all acceptance tests pass.")
            info("NEXT: `python factory.py maintain`.")
        else:
            info("tests pass.")
        return
    miss = harness_failure(plan.get("test", ""), tail)
    if miss:
        die_harness(miss, plan.get("test", ""))
    state["fixing"] = state["phase"] == "build"
    save_state(state)
    if state["phase"] != "build":
        atomic_write(LOG_F, tail + "\n")
    write_bundle(bundle_fix(state, plan, tail)
                 if state["phase"] == "build"
                 else bundle_maintain(state, plan))
    info("tests failing - bundle written. NEXT: upload bundle.md.")


def cmd_maintain(args):
    state = load_state() or die("nothing built yet")
    plan = load_plan() or die("no blueprint yet")
    if state["phase"] == "maintain":
        info("already in maintenance.")
    elif state["phase"] != "built" and not args.force:
        die("build not verified yet (use --force to switch anyway)")
    state.update(phase="maintain", fixing=False,
                 session_next=max(state["session_next"], 1))
    save_state(state)
    git_commit("enter maintenance phase")
    info("maintenance loop: 1) `python factory.py run` reproduces a bug "
         "(or write artifacts/request.md + `python factory.py bundle`)  "
         "2) upload bundle.md  3) paste reply -> payload.md  "
         "4) `python factory.py apply`. Repeat.")
    write_bundle(current_bundle(state, plan))


def cmd_bundle(args):
    state = load_state() or die("run `python factory.py apply` first")
    plan = load_plan()
    if not plan:
        die("no blueprint yet - session 1 comes from seed.md, not a bundle")
    write_bundle(current_bundle(state, plan))


def cmd_status(args):
    state = load_state()
    if not state:
        print("fresh project.\n"
              "  1. new chat: upload seed.md + paste your project "
              "description\n"
              "  2. save the reply into payload.md\n"
              "  3. python factory.py apply")
        return
    plan = load_plan()
    tot = (f"/{state['sessions_total']}"
           if state.get("sessions_total") and state["phase"] == "build" else "")
    print(f"phase: {state['phase']}   next session/cycle: "
          f"{state['session_next']}{tot}"
          + ("   [fixing]" if state.get("fixing") else "")
          + ("   [awaiting resume]" if state.get("resume") else ""))
    if plan:
        print(f"project: {plan['project']}   test: {plan.get('test')}")
    c = copilot_cfg()
    print("copilot: " + (f"{c['style']} / {c['model']}" if c else
          "off (set GROQ_API_KEY or GEMINI_API_KEY for advisory help)"))
    if ERROR_F.exists():
        print("last error:\n" + read_text(ERROR_F))
    print("next step: " + ("upload bundle.md, paste reply -> payload.md, "
                           "then `python factory.py apply`"))


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="factory.py",
                                 description="LLM app-factory runner")
    sub = ap.add_subparsers(dest="verb")
    p = sub.add_parser("apply", help="validate + apply payload, emit next "
                                     "bundle")
    p.add_argument("payload", nargs="?", help="payload file "
                                              "(default payload.md)")
    p.add_argument("--clip", action="store_true",
                   help="read payload from the clipboard")
    sub.add_parser("bundle", help="(re)write bundle.md from current state")
    r = sub.add_parser("run", help="run the app, capture trimmed log, bundle")
    r.add_argument("--cmd", help="override the pinned run command")
    sub.add_parser("test", help="run the pinned test command")
    m = sub.add_parser("maintain", help="switch to the maintenance loop")
    m.add_argument("--force", action="store_true")
    sub.add_parser("status", help="show state and next step")
    sub.add_parser("spec", help="print the canonical payload spec")
    args = ap.parse_args()

    os.chdir(ROOT)
    {"apply": cmd_apply, "bundle": cmd_bundle, "run": cmd_run,
     "test": cmd_test, "maintain": cmd_maintain, "spec":
         lambda a: print(SPEC), "status": cmd_status,
     None: cmd_status}[args.verb](args)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
