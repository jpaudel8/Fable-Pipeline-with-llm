# manifest (machine-extracted ground truth; outranks memory)

## files
README.md (211 lines, 11218 B)
docker-compose.yml (19 lines, 349 B)
seed.md (145 lines, 7175 B)
backend/Dockerfile (9 lines, 195 B)
backend/db.js (91 lines, 2547 B)
backend/groq.js (61 lines, 1884 B)
backend/package.json (13 lines, 199 B)
backend/server.js (79 lines, 2405 B)
frontend/Dockerfile (8 lines, 160 B)
frontend/index.html (13 lines, 299 B)
frontend/package.json (21 lines, 394 B)
frontend/vite.config.js (9 lines, 252 B)
frontend/src/App.jsx (36 lines, 1262 B)
frontend/src/api.js (66 lines, 1941 B)
frontend/src/index.css (2 lines, 23 B)
frontend/src/main.jsx (6 lines, 160 B)
frontend/src/components/ChatWindow.jsx (108 lines, 3041 B)
frontend/src/components/ModelSwitcher.jsx (25 lines, 544 B)
frontend/src/components/Sidebar.jsx (28 lines, 950 B)
tests/test_acceptance.js (161 lines, 6994 B)

## interfaces
### backend/db.js
function ensureInit()
export function initDb(dbPath)
export function createConversation(model)
export function listConversations()
export function getConversation(id)
export function listMessages(conversationId)
export function addMessage(conversationId, role, content)
export function touchConversation(id, updates = {})
### backend/server.js
route GET /api/health
route GET /api/models
route GET /api/conversations
route POST /api/conversations
route GET /api/conversations/:id/messages
route POST /api/conversations/:id/messages
### frontend/src/App.jsx
export default function App()
const refreshConversations = async () => setConversations(await listConversations());
const handleNewChat = async () =>
### frontend/src/api.js
export async function listModels()
export async function listConversations()
export async function createConversation(model)
export async function listMessages(conversationId)
export function streamMessage(conversationId, content, model, { onDelta, onDone, onError })
### frontend/src/components/ChatWindow.jsx
export default function ChatWindow({ conversationId, model, onMessageSent })
function handleSend()
### frontend/src/components/ModelSwitcher.jsx
export default function ModelSwitcher({ model, onChange })
### frontend/src/components/Sidebar.jsx
export default function Sidebar({ conversations, selectedId, onSelect, onNewChat })
### tests/test_acceptance.js
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitForBackendHealth(label)
async function waitForFrontend()
async function readSseEvents(response)
