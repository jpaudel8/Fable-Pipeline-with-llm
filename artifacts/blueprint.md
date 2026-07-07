## OVERVIEW

Groq Chat UI: a two-service local chat app. A React+Vite frontend (light theme,
Tailwind v4 defaults) is called directly from the browser by client JS; an
Express backend proxies chat completions to Groq's OpenAI-compatible streaming
API and relays deltas to the browser over Server-Sent Events. SQLite
(better-sqlite3, WAL mode, file under a mounted volume) persists conversations
and messages across restarts. Frontend pieces: Sidebar (conversation list +
New Chat button), ModelSwitcher (dropdown over 4 pinned Groq models),
ChatWindow (message list + streaming input). Flow: user sends a message ->
ChatWindow POSTs to backend -> backend stores the user message, streams Groq
deltas back over SSE -> frontend renders incrementally -> backend stores the
final assistant message -> Sidebar refreshes (title/order). Docker Compose
runs both services; ports 5173 (frontend, browser-facing) and 3001 (backend,
called directly by browser JS at http://localhost:3001, no dev-proxy).
GROQ_API_KEY is supplied via .env.

```json
{
  "project": "groq-chat-ui",
  "run": "docker compose up --build",
  "test": "docker compose up --build -d && node tests/test_acceptance.js; CODE=$?; docker compose down -v; exit $CODE",
  "budget_lines": 350,
  "env": ["GROQ_API_KEY"],
  "runtime": {
    "node": "22.12.0",
    "deps": {
      "express": "5.2.1",
      "cors": "2.8.6",
      "dotenv": "17.4.2",
      "better-sqlite3": "12.11.1",
      "react": "19.2.7",
      "react-dom": "19.2.7",
      "vite": "8.1.3",
      "@vitejs/plugin-react": "6.0.3",
      "tailwindcss": "4.3.2",
      "@tailwindcss/vite": "4.3.2"
    }
  },
  "sessions": [
    {
      "id": 2,
      "title": "Backend data layer (SQLite)",
      "model": "small",
      "files": ["backend/db.js"],
      "task": "ESM module. Export: initDb(dbPath) — fs.mkdirSync(path.dirname(dbPath),{recursive:true}); open `new Database(dbPath)` from 'better-sqlite3'; db.pragma('journal_mode = WAL'); CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, title TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL). Export createConversation(model) — id=crypto.randomUUID() (import { randomUUID } from 'node:crypto'), title='New Chat', created_at=updated_at=new Date().toISOString(); INSERT then return the row object. Export listConversations() — SELECT * FROM conversations ORDER BY updated_at DESC. Export getConversation(id) — SELECT * FROM conversations WHERE id=? (returns undefined if absent). Export listMessages(conversationId) — SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC. Export addMessage(conversationId, role, content) — id=crypto.randomUUID(), created_at=new Date().toISOString(); INSERT then return {id, role, content, created_at}. Export touchConversation(id, updates = {}) — always UPDATE updated_at=new Date().toISOString() WHERE id=?; additionally SET title=? when updates.title is given, and/or SET model=? when updates.model is given (single UPDATE statement built from whichever fields are present). Keep the db instance module-scoped, created on initDb() call; all other exports throw if called before initDb().",
      "interfaces": [
        "export function initDb(dbPath: string): void",
        "export function createConversation(model: string): {id,title,model,created_at,updated_at}",
        "export function listConversations(): Array<{id,title,model,created_at,updated_at}>",
        "export function getConversation(id: string): {id,title,model,created_at,updated_at}|undefined",
        "export function listMessages(conversationId: string): Array<{id,role,content,created_at}>",
        "export function addMessage(conversationId: string, role: 'user'|'assistant', content: string): {id,role,content,created_at}",
        "export function touchConversation(id: string, updates?: {title?: string, model?: string}): void"
      ],
      "uses": []
    },
    {
      "id": 3,
      "title": "Groq streaming client",
      "model": "big",
      "files": ["backend/groq.js"],
      "task": "ESM module, no npm deps beyond builtins (use global fetch, Node 22). Export GROQ_MODELS: a frozen array of exactly the 4 objects listed in interfaces (id/label pairs) — this is Groq's current production, non-vision chat model set as of July 2026; do not add/remove entries. Export async generator streamGroqChat(model, messages): POST 'https://api.groq.com/openai/v1/chat/completions' with headers {Authorization: `Bearer ${process.env.GROQ_API_KEY}`, 'Content-Type': 'application/json'} and body JSON.stringify({model, messages, stream: true}). If !response.ok, throw new Error(`Groq API error ${response.status}: ${await response.text()}`). Otherwise get a reader via response.body.getReader(), decode chunks with `new TextDecoder()`, append to a string buffer, repeatedly split the buffer on the first '\\n\\n' to extract complete SSE events (keep any remainder in the buffer for the next chunk). For each event: trim it; if it doesn't start with 'data: ', skip it; take the substring after 'data: '; if it === '[DONE]', return (end generator); otherwise JSON.parse it and, if `parsed.choices?.[0]?.delta?.content` is a non-empty string, yield that string. Continue reading chunks until the reader signals done, then process any final buffered event the same way.",
      "interfaces": [
        "export const GROQ_MODELS = [{id:'llama-3.3-70b-versatile',label:'Llama 3.3 70B'},{id:'llama-3.1-8b-instant',label:'Llama 3.1 8B (fast)'},{id:'openai/gpt-oss-120b',label:'GPT-OSS 120B'},{id:'openai/gpt-oss-20b',label:'GPT-OSS 20B (fast)'}]",
        "export async function* streamGroqChat(model: string, messages: Array<{role:'user'|'assistant', content:string}>): AsyncGenerator<string>"
      ],
      "uses": []
    },
    {
      "id": 4,
      "title": "Frontend API client + Sidebar",
      "model": "big",
      "files": ["frontend/src/api.js", "frontend/src/components/Sidebar.jsx"],
      "task": "api.js (ESM): const API_BASE = 'http://localhost:3001'. listModels() -> fetch(`${API_BASE}/api/models`).then(r=>r.json()). listConversations() -> fetch(`${API_BASE}/api/conversations`).then(r=>r.json()). createConversation(model) -> fetch(`${API_BASE}/api/conversations`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({model})}).then(r=>r.json()). listMessages(conversationId) -> fetch(`${API_BASE}/api/conversations/${conversationId}/messages`).then(r=>r.json()). streamMessage(conversationId, content, model, {onDelta, onDone, onError}) -> fetch POST `${API_BASE}/api/conversations/${conversationId}/messages` with JSON body {content, model}; on response, read response.body.getReader(), decode with TextDecoder, buffer text, split on '\\n\\n' into events same framing as groq.js, parse JSON after 'data: ' prefix, and call onDelta(ev.delta) when ev.delta is present, onDone(ev.message) when ev.done is true, onError(ev.error) when ev.error is present; wrap the whole thing in an async IIFE and call onError(err.message) on any thrown/network error. Sidebar.jsx: default-export function Sidebar({conversations, selectedId, onSelect, onNewChat}) rendering a '+ New Chat' button (onClick=onNewChat) above a scrollable list; each conversation renders as a button showing conv.title, calling onSelect(conv.id) on click, with a highlighted (different bg) style when conv.id === selectedId. Tailwind utility classes only, light theme (white/gray-50 background, gray-200 borders, blue-600 accent for selected/buttons), no custom config.",
      "interfaces": [
        "export async function listModels(): Promise<Array<{id:string,label:string}>>",
        "export async function listConversations(): Promise<Array<{id,title,model,created_at,updated_at}>>",
        "export async function createConversation(model: string): Promise<{id,title,model,created_at,updated_at}>",
        "export async function listMessages(conversationId: string): Promise<Array<{id,role,content,created_at}>>",
        "export function streamMessage(conversationId: string, content: string, model: string, callbacks: {onDelta:(text:string)=>void, onDone:(message:object)=>void, onError:(msg:string)=>void}): void",
        "export default function Sidebar({conversations, selectedId, onSelect, onNewChat}): JSX.Element"
      ],
      "uses": []
    },
    {
      "id": 5,
      "title": "ModelSwitcher + ChatWindow",
      "model": "big",
      "files": ["frontend/src/components/ModelSwitcher.jsx", "frontend/src/components/ChatWindow.jsx"],
      "task": "ModelSwitcher.jsx: default-export function ModelSwitcher({model, onChange}); on mount (useEffect, empty deps) call listModels() from '../api.js' into local state `models` (array, default []); render a <select value={model} onChange={e=>onChange(e.target.value)}> with an <option> per model {id,label}; Tailwind: border, rounded, px-2 py-1, bg-white. ChatWindow.jsx: default-export function ChatWindow({conversationId, model, onMessageSent}); local state messages=[] and streamingText=''and inputValue=''; useEffect on [conversationId]: if conversationId is set, call listMessages(conversationId) from '../api.js' and setMessages(result), else setMessages([]); also clear streamingText. handleSend(): if !conversationId or !inputValue.trim() return; const text=inputValue.trim(); setInputValue(''); setMessages(prev=>[...prev, {id:'local-'+Date.now(), role:'user', content:text, created_at:new Date().toISOString()}]); call streamMessage(conversationId, text, model, {onDelta: t=>setStreamingText(prev=>prev+t), onDone: message=>{setMessages(prev=>[...prev, message]); setStreamingText(''); onMessageSent && onMessageSent();}, onError: msg=>{setStreamingText(''); setMessages(prev=>[...prev, {id:'err-'+Date.now(), role:'assistant', content:'Error: '+msg, created_at:new Date().toISOString()}]);}}). Render: scrollable message list (user bubbles right-aligned blue-600 text-white, assistant bubbles left-aligned gray-100 text-gray-900, rounded-2xl px-4 py-2), a trailing streaming bubble when streamingText is non-empty, and a bottom input row (text input flex-1 border rounded px-3 py-2, Send button bg-blue-600 text-white rounded px-4 py-2) that calls handleSend on click or Enter keydown. If !conversationId, render a centered placeholder ('Select or start a new chat').",
      "interfaces": [
        "export default function ModelSwitcher({model: string, onChange: (modelId:string)=>void}): JSX.Element",
        "export default function ChatWindow({conversationId: string|null, model: string, onMessageSent?: ()=>void}): JSX.Element"
      ],
      "uses": [4]
    },
    {
      "id": 6,
      "title": "Backend wiring (Express app + Dockerfile)",
      "model": "small",
      "files": ["backend/server.js", "backend/package.json", "backend/Dockerfile"],
      "task": "server.js (ESM): import 'dotenv/config' first; import express, cors, { initDb, createConversation, listConversations, getConversation, listMessages, addMessage, touchConversation } from './db.js', { GROQ_MODELS, streamGroqChat } from './groq.js'. Call initDb(process.env.DB_PATH || './data/chat.db'). const app=express(); app.use(cors({origin: process.env.CORS_ORIGIN || 'http://localhost:5173'})); app.use(express.json()). Routes exactly as listed in interfaces below. For the streaming route: after validating, set res.writeHead(200, {'Content-Type':'text/event-stream','Cache-Control':'no-cache',Connection:'keep-alive'}); build history=listMessages(id).map(m=>({role:m.role, content:m.content})); const useModel = req.body.model || getConversation(id).model; let fullText=''; try { for await (const delta of streamGroqChat(useModel, history)) { fullText += delta; res.write(`data: ${JSON.stringify({delta})}\\n\\n`); } const saved = addMessage(id, 'assistant', fullText); touchConversation(id, {}); res.write(`data: ${JSON.stringify({done:true, message:saved})}\\n\\n`); } catch (err) { res.write(`data: ${JSON.stringify({error: err.message})}\\n\\n`); } res.end(); . Listen on process.env.PORT || 3001. package.json: {\"type\":\"module\",\"scripts\":{\"start\":\"node server.js\"},\"dependencies\":{express,cors,dotenv,better-sqlite3 at the pinned versions}}. Dockerfile: FROM node:22.12.0-alpine; WORKDIR /app; RUN apk add --no-cache python3 make g++ (build toolchain for better-sqlite3's native binding fallback); COPY package.json ./; RUN npm install --omit=dev; COPY db.js groq.js server.js ./; EXPOSE 3001; CMD [\"node\",\"server.js\"].",
      "interfaces": [
        "GET /api/health -> 200 {status:'ok'}",
        "GET /api/models -> 200 GROQ_MODELS",
        "GET /api/conversations -> 200 listConversations()",
        "POST /api/conversations body {model?: string} -> 201 createConversation(model || GROQ_MODELS[0].id)",
        "GET /api/conversations/:id/messages -> 200 listMessages(id); 404 {error:'not found'} if getConversation(id) is undefined",
        "POST /api/conversations/:id/messages body {content: string, model?: string} -> 404 {error:'not found'} if conversation missing; 400 {error:'content is required'} if !content; else 200 text/event-stream: before streaming, addMessage(id,'user',content), then touchConversation(id, listMessages(id).length===1 ? {title: content.slice(0,40).trim()} : {}); events are lines `data: <json>\\n\\n` shaped {delta:string} | {done:true, message:{id,role,content,created_at}} | {error:string}"
      ],
      "uses": [2, 3]
    },
    {
      "id": 7,
      "title": "Frontend wiring + Docker Compose",
      "model": "small",
      "files": [
        "frontend/src/App.jsx",
        "frontend/src/main.jsx",
        "frontend/src/index.css",
        "frontend/index.html",
        "frontend/vite.config.js",
        "frontend/package.json",
        "frontend/Dockerfile",
        "docker-compose.yml"
      ],
      "task": "App.jsx: state conversations=[] (from listConversations() in api.js, loaded on mount and after refreshConversations()), selectedId=null, model='llama-3.3-70b-versatile'. refreshConversations = async()=>setConversations(await listConversations()). handleNewChat = async()=>{ const conv=await createConversation(model); await refreshConversations(); setSelectedId(conv.id); }. Layout: <div className='flex h-screen bg-white text-gray-900'><Sidebar conversations={conversations} selectedId={selectedId} onSelect={setSelectedId} onNewChat={handleNewChat}/><main className='flex-1 flex flex-col'><div className='border-b p-2'><ModelSwitcher model={model} onChange={setModel}/></div><ChatWindow conversationId={selectedId} model={model} onMessageSent={refreshConversations}/></main></div>. main.jsx: import './index.css'; import App from './App.jsx'; import {createRoot} from 'react-dom/client'; createRoot(document.getElementById('root')).render(<App/>). index.css: single line `@import \"tailwindcss\";`. index.html: minimal HTML5 doc, <div id='root'></div>, <script type='module' src='/src/main.jsx'></script>. vite.config.js: import {defineConfig} from 'vite'; import react from '@vitejs/plugin-react'; import tailwindcss from '@tailwindcss/vite'; export default defineConfig({plugins:[react(), tailwindcss()], server:{host:true, port:5173, strictPort:true}}). package.json: {\"type\":\"module\",\"scripts\":{\"dev\":\"vite\",\"build\":\"vite build\",\"preview\":\"vite preview\"},\"dependencies\":{react,react-dom at pinned versions},\"devDependencies\":{vite,@vitejs/plugin-react,tailwindcss,@tailwindcss/vite at pinned versions}}. Dockerfile: FROM node:22.12.0-alpine; WORKDIR /app; COPY package.json ./; RUN npm install; COPY . .; EXPOSE 5173; CMD [\"npm\",\"run\",\"dev\",\"--\",\"--host\",\"0.0.0.0\",\"--port\",\"5173\"]. docker-compose.yml: services.backend {build: ./backend, ports:['3001:3001'], env_file: .env, environment:{PORT:'3001', DB_PATH:'/app/data/chat.db', CORS_ORIGIN:'http://localhost:5173'}, volumes:['./backend/data:/app/data']}; services.frontend {build: ./frontend, ports:['5173:5173'], depends_on:['backend']}.",
      "interfaces": [],
      "uses": [4, 5, 6]
    },
    {
      "id": 8,
      "title": "integration & verification",
      "verify": true,
      "model": "big",
      "files": ["tests/test_acceptance.js"],
      "task": "Plain Node ESM script, builtins only (global fetch, node:assert/strict, node:child_process execSync). Assume `docker compose up --build -d` already ran (the pinned test command does this before invoking this file). 1) Poll `http://localhost:3001/api/health` every 1s up to 60 tries until it returns 200 with {status:'ok'}; fail loudly if timeout. 2) Poll `http://localhost:5173/` until it returns 200. 3) GET `http://localhost:3001/api/models`; assert it's an array of exactly 4 items and its ids include 'llama-3.3-70b-versatile','llama-3.1-8b-instant','openai/gpt-oss-120b','openai/gpt-oss-20b'. 4) POST `http://localhost:3001/api/conversations` with {} body; assert 201 and capture conv.id; GET conversations list and assert it contains that id. 5) POST `http://localhost:3001/api/conversations/${id}/messages` with {content:'Say the single word: hello', model:'llama-3.1-8b-instant'}; read the SSE body via response.body.getReader()+TextDecoder using the same '\\n\\n'/'data: ' framing described for groq.js; assert at least one {delta} event and exactly one terminal {done:true, message} event were received, and message.role==='assistant'. 6) GET `http://localhost:3001/api/conversations/${id}/messages`; assert it returns exactly 2 messages, ordered [user, assistant], and GET the conversation via the list endpoint to assert its title is no longer the literal 'New Chat'. 7) Run `execSync('docker compose restart backend')`, then repeat the health-poll from step 1 against port 3001. 8) Re-GET `http://localhost:3001/api/conversations/${id}/messages` and assert the same 2 messages are still present (persistence across restart via the mounted volume). Print a clear PASS/FAIL summary; process.exit(1) on any failed assertion, process.exit(0) if all pass (let uncaught assertion errors propagate and set a non-zero exit naturally).",
      "interfaces": [],
      "uses": [6, 7]
    }
  ],
  "acceptance": [
    "GET /api/health returns 200 {status:'ok'} after startup",
    "GET / on the frontend (5173) returns 200 after startup",
    "GET /api/models returns exactly the 4 pinned Groq model ids",
    "POST /api/conversations creates a conversation visible in GET /api/conversations",
    "Sending a message streams >=1 delta SSE event followed by one done event with an assistant message",
    "GET /api/conversations/:id/messages returns exactly 2 messages in order user, assistant after one exchange",
    "The conversation's title auto-updates away from the default 'New Chat' after the first message",
    "After `docker compose restart backend`, the same 2 messages are still returned (SQLite persists via the mounted volume)"
  ]
}
```

## NOTES

- Groq deprecated `mixtral-8x7b-32768` (removed) and `gemma2-9b-it` (removed);
  the model switcher uses Groq's current production chat models instead:
  2x Llama (3.3 70B, 3.1 8B) + 2x GPT-OSS (120B, 20B), satisfying "model
  switcher, e.g. llama/etc" with what Groq actually serves today.
- Frontend calls the backend directly at `http://localhost:3001` (no Vite
  dev-proxy) since both ports are published to the host by Compose; this is
  simplest for a local tool and matches the fixed 5173/3001 ports requested.
- The acceptance test's message-send step makes one real call to Groq, so a
  working `GROQ_API_KEY` must be in `.env` for `test` to pass — this is
  inherent to testing a live API proxy.
- No binary assets (icons/fonts/images) are required; UI is plain Tailwind
  utility classes (light/white, gray borders, blue-600 accent).
- SQLite file lives at `backend/data/chat.db`, bind-mounted via Compose so it
  survives container restarts/rebuilds.
