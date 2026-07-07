# LLM App Factory — fat runner, thin payloads

Turn any chat-only LLM into a multi-session application factory where the
model outputs **only source code and patches** (the expensive tokens) and a
permanent local script, `factory.py`, does everything mechanical for free:
validation, git safety, manifest extraction, log capture, and assembling the
next session's context into a single upload file.

Output tokens cost ~5x input tokens, so the design rule is: anything static
or deterministic is runner code or bundle input; the LLM never re-emits
boilerplate, protocols, handoffs, or state.

## Requirements

Python ≥ 3.9 and git on your PATH. No packages to install. Optional: `node`
(enables JS syntax checks), Docker (default run command).

## Quickstart

```
mkdir myapp && cd myapp        # empty folder
cp .../factory.py .            # the runner lives here forever
```

1. **Session 1 (architect):** new chat → upload `seed.md` → paste your project
   description. Save the entire reply into `payload.md` in the project folder.
2. `python factory.py apply` — scaffolds the repo, validates and installs the
   blueprint, generates `.env.example` + `README`, and writes **`bundle.md`**.
3. **Every later session:** fresh chat → upload the single file `bundle.md`
   **and send the one-line message the runner prints** (assistants rightly
   ignore instructions that live only inside uploaded files, so your chat
   message supplies the intent) → save the reply into `payload.md` →
   `python factory.py apply`. Repeat. The bundle header tells you which
   model tier (`big`/`small`) to use.
4. The final session's apply auto-runs the pinned test command. Failures loop
   as fix bundles until green, then: `python factory.py maintain`.

**Maintenance loop:** `python factory.py run` starts the app (default
`docker compose up --build`), tees output to your terminal, and keeps the last
200 log lines; on exit it writes a diagnosis bundle with traceback-anchored
source slices. For features or silent bugs, write your intent into
`artifacts/request.md` and run `python factory.py bundle`. Then upload
`bundle.md` → paste reply → `apply`, as always.

Your total manual work per turn: upload one file, paste one reply, run one
command. Cut-off replies need no special action — paste what arrived and
`apply`; the runner detects truncation and produces a resume bundle that asks
only for the missing blocks.

## Commands

```
python factory.py            status + your exact next step
python factory.py apply      validate & apply payload.md, emit next bundle
       apply --clip          read the payload from the clipboard instead
python factory.py run        run the app, capture trimmed log, emit bundle
python factory.py bundle     re-emit bundle.md (e.g. after editing request.md)
python factory.py test       run the pinned acceptance tests now
python factory.py maintain   switch phases after a verified build
python factory.py spec       print the canonical payload spec
```

## Safety model

- **Two-phase apply.** Every payload is fully validated in memory first:
  begin/end markers and block counts, path safety (project-root jail, `.git`
  and runner files protected), patch anchors unique, Python compiled, JSON
  parsed. Only then does anything touch disk — all-or-nothing.
- **Git as the undo button.** Manual edits are auto-committed before each
  apply; a snapshot is taken; any post-write failure triggers
  `git reset --hard` + clean back to the snapshot. Every applied session is
  one commit, so history is a ladder of known states.
- **Frozen contract.** The blueprint (pinned versions + interfaces) is
  writable only by session 1; later changes require you to opt in with
  `ALLOW_BLUEPRINT=1 python factory.py apply`.
- **Truncation-proof.** Missing end marker → nothing is applied; received
  blocks are stored and a resume bundle requests only the remainder.
- **Stale-upload guard.** Payloads carry their session number; a mismatch
  with local state is rejected before validation.
- **No secrets.** `.env` is user-managed and protected; the spec forbids
  secret values, allowing env var names only.
- Heads-up: the `run`/`test` commands executed by the runner are the ones
  pinned in *your* blueprint — review them once at session 1, like any
  Makefile.
- **Harness vs app failures.** If the pinned test command itself can't start
  (e.g. `No module named pytest` on the host), the runner pauses the loop and
  tells you — no fix bundle is sent, since no source patch can install a host
  package. After you install it (or edit the test command in the blueprint
  and commit), `python factory.py test` finishes verification. And when a
  path exists on the host but a dockerized command can't find it, the failure
  is annotated with a RUNNER HINT so the session patches the Dockerfile or
  volumes instead of chasing app logic.

## Why builds fail at the end — and what the seed now enforces

End-stage errors almost always trace back to planning defects, not payload
mechanics: forward dependencies (entry files written before the modules they
import), versions pinned from memory, contracts vague enough for two sessions
to implement differently, and files that other files reference but no session
owns. seed.md therefore carries a pre-write checklist the architect must
resolve before emitting the blueprint (dependency-ordered sessions with
entry/composition files last, reference completeness including what dockerized
tests must see, search-verified version pins, ambiguous operations spelled as
literal import+call lines). The runner enforces what it can mechanically:
`uses` may only point to earlier sessions, and a build session cannot touch a
file the plan assigns to someone else. Mid-build discoveries survive:
sessions flag contradictions as `flagged_correction:` notes and new shared
facts as `contract:` notes, and every bundle carries the accumulated notes —
so session 7 honors what session 3 learned.

## Free-model copilot (optional)

Export one key and the runner recruits a free LLM for the transitions —
never for application source code, which stays with your advanced model:

```
export GROQ_API_KEY=...      # llama-3.3-70b-versatile via api.groq.com
# or: export GEMINI_API_KEY=...   # gemini-2.5-flash via v1beta
# or point anywhere OpenAI-compatible:
#   COPILOT_URL=... COPILOT_KEY=... [COPILOT_MODEL=...] [COPILOT_STYLE=gemini]
# disable anytime: COPILOT=off
```

Four advisory roles. **Repair**: when a pasted reply is structurally broken
(markdown fences, prose wrapping, wrong block counts), the copilot proposes a
format-only fix; the runner accepts it only if it re-parses cleanly AND every
code/content line is byte-identical to the original — otherwise it's
discarded and you see the normal error. This turns wasted advanced-model
resend turns into free ones. **Triage**: fix and maintain bundles gain a
short root-cause hypothesis, clearly labeled as advisory and possibly wrong,
under the real evidence. **Manifest slimming**: once the manifest exceeds
~6KB (COPILOT_SLIM_AT), interface signatures are filtered to files the
copilot judges relevant, always unioned with the deterministic must-haves
(your session's files, `uses` dependencies, traceback paths); the complete
file list is never filtered, and `#%% need` recovers anything missed.
**Blueprint review**: after session 1, an advisory checklist audit lands in
artifacts/review.md — warnings for you, never a blocker.

The trust model is one sentence: free models advise, deterministic code
decides. Every copilot failure mode — no key, 429s, timeouts, nonsense
output — degrades to exactly the runner's normal behavior, so the pipeline
works identically with the feature off. Privacy note: enabling it sends code
excerpts, logs, and your blueprint to that provider. Free-tier headroom is
ample for this usage (a handful of small calls per session; Groq's free
llama-3.3-70b allows ~1K requests/day — switch COPILOT_MODEL to
llama-3.1-8b-instant for 14.4K/day if you ever hit limits).

## Token economics

- The LLM emits raw file/patch payloads — no per-session script boilerplate,
  no string escaping, no handoff/state/manifest prose. Expect roughly half
  the output tokens of a "model emits scripts" design, more in maintenance.
- Bundles are assembled deterministically: spec + your task + only the
  contract slice you `use` + machine manifest + error/log slices. Input is
  cheap; irrelevant context is still excluded to keep it lean.
- One-turn debugging: the runner parses tracebacks and pre-slices ±40 lines
  around each in-project frame into the bundle, so most bugs skip the
  "request review → patch" round trip. When context is missing, the model
  spends ~3 lines on a `#%% need` block instead of a review script.
- `model: big|small` tags in the blueprint route mechanical sessions to a
  10–20x cheaper model; the bundle carries everything they need.

## Payload spec (what the LLM replies with)

```
PAYLOAD SPEC v1 - your ENTIRE reply is one payload. No prose outside it.

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
```

## Files at a glance

```
factory.py               the runner (permanent, human-audited, never LLM-written)
seed.md                  upload once, session 1 only
bundle.md                runner output — the ONE file you upload each turn
payload.md               where you paste each LLM reply
artifacts/blueprint.md   frozen plan + tech contract (```json plan inside)
artifacts/manifest.md    machine-extracted signatures — ground truth
artifacts/state.json     phase / session / resume bookkeeping (runner-owned)
artifacts/request.md     you write feature requests / bug intent here
artifacts/log.txt        last 200 runtime log lines (auto-captured)
artifacts/notes.md       accumulated session memos
```
