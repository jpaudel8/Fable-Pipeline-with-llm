# manifest (machine-extracted ground truth; outranks memory)

## files
README.md (211 lines, 11218 B)
seed.md (145 lines, 7175 B)
backend/db.js (91 lines, 2547 B)

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
