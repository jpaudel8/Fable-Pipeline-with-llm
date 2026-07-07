import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';

const BACKEND_URL = 'http://localhost:3001';
const FRONTEND_URL = 'http://localhost:5173';
const HEALTH_MAX_TRIES = 60;
const HEALTH_INTERVAL_MS = 1000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForBackendHealth(label) {
  for (let attempt = 1; attempt <= HEALTH_MAX_TRIES; attempt++) {
    try {
      const res = await fetch(`${BACKEND_URL}/api/health`);
      if (res.status === 200) {
        const body = await res.json();
        if (body && body.status === 'ok') {
          console.log(`[PASS] backend health OK (${label}), attempt ${attempt}/${HEALTH_MAX_TRIES}`);
          return;
        }
      }
    } catch {
      // backend not accepting connections yet, keep polling
    }
    await sleep(HEALTH_INTERVAL_MS);
  }
  throw new Error(`FAIL: backend /api/health did not return 200 {status:'ok'} within ${HEALTH_MAX_TRIES}s (${label})`);
}

async function waitForFrontend() {
  for (let attempt = 1; attempt <= HEALTH_MAX_TRIES; attempt++) {
    try {
      const res = await fetch(FRONTEND_URL + '/');
      if (res.status === 200) {
        console.log(`[PASS] frontend reachable, attempt ${attempt}/${HEALTH_MAX_TRIES}`);
        return;
      }
    } catch {
      // frontend not up yet, keep polling
    }
    await sleep(HEALTH_INTERVAL_MS);
  }
  throw new Error(`FAIL: frontend / did not return 200 within ${HEALTH_MAX_TRIES}s`);
}

async function readSseEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const events = [];
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const dataLine = block.split('\n').find((l) => l.startsWith('data: '));
      if (!dataLine) continue;
      try {
        events.push(JSON.parse(dataLine.slice('data: '.length)));
      } catch {
        // malformed event, skip - matches frontend streamMessage tolerance
      }
    }
  }
  return events;
}

// 1. backend health after startup
await waitForBackendHealth('startup');

// 2. frontend reachable
await waitForFrontend();

// 3. /api/models: exactly 4 pinned ids
const modelsRes = await fetch(`${BACKEND_URL}/api/models`);
assert.equal(modelsRes.status, 200, '/api/models should return 200');
const models = await modelsRes.json();
assert.ok(Array.isArray(models), '/api/models should return an array');
assert.equal(models.length, 4, '/api/models should return exactly 4 items');
const modelIds = models.map((m) => m.id);
for (const id of [
  'llama-3.3-70b-versatile',
  'llama-3.1-8b-instant',
  'openai/gpt-oss-120b',
  'openai/gpt-oss-20b',
]) {
  assert.ok(modelIds.includes(id), `/api/models missing expected id ${id}`);
}
console.log('[PASS] /api/models returns exactly the 4 pinned Groq model ids');

// 4. create conversation, verify visible in list
const createRes = await fetch(`${BACKEND_URL}/api/conversations`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({}),
});
assert.equal(createRes.status, 201, 'POST /api/conversations should return 201');
const conv = await createRes.json();
assert.ok(conv && conv.id, 'created conversation should have an id');
const convId = conv.id;

const listRes = await fetch(`${BACKEND_URL}/api/conversations`);
assert.equal(listRes.status, 200, 'GET /api/conversations should return 200');
const list = await listRes.json();
assert.ok(list.some((c) => c.id === convId), 'GET /api/conversations should include the new conversation');
console.log('[PASS] POST /api/conversations creates a conversation visible in GET /api/conversations');

// 5. send message, verify SSE stream shape
const msgRes = await fetch(`${BACKEND_URL}/api/conversations/${convId}/messages`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ content: 'Say the single word: hello', model: 'llama-3.1-8b-instant' }),
});
assert.equal(msgRes.status, 200, 'POST .../messages should return 200 text/event-stream');
const events = await readSseEvents(msgRes);
const deltaEvents = events.filter((e) => typeof e.delta === 'string');
const doneEvents = events.filter((e) => e.done === true);
assert.ok(deltaEvents.length >= 1, 'expected at least one delta event');
assert.equal(doneEvents.length, 1, 'expected exactly one terminal done event');
assert.ok(doneEvents[0].message, 'done event should include a message');
assert.equal(doneEvents[0].message.role, 'assistant', 'done event message role should be assistant');
console.log('[PASS] message stream produced >=1 delta event and one done event with assistant message');

// 6. messages persisted, ordered; title auto-updated
const messagesRes = await fetch(`${BACKEND_URL}/api/conversations/${convId}/messages`);
assert.equal(messagesRes.status, 200, 'GET messages should return 200');
const messagesBefore = await messagesRes.json();
assert.equal(messagesBefore.length, 2, 'expected exactly 2 messages after one exchange');
assert.equal(messagesBefore[0].role, 'user', 'first message should be from user');
assert.equal(messagesBefore[1].role, 'assistant', 'second message should be from assistant');
console.log('[PASS] GET messages returns exactly 2 messages ordered [user, assistant]');

const listRes2 = await fetch(`${BACKEND_URL}/api/conversations`);
const list2 = await listRes2.json();
const convAfterMsg = list2.find((c) => c.id === convId);
assert.ok(convAfterMsg, 'conversation should still be present in list');
assert.notEqual(convAfterMsg.title, 'New Chat', 'title should have auto-updated away from "New Chat"');
console.log('[PASS] conversation title auto-updated away from default "New Chat"');

// 7. restart backend, re-poll health
console.log('[INFO] restarting backend container...');
execSync('docker compose restart backend', { stdio: 'inherit' });
await waitForBackendHealth('post-restart');

// 8. messages still present after restart (SQLite volume persistence)
const messagesRes2 = await fetch(`${BACKEND_URL}/api/conversations/${convId}/messages`);
assert.equal(messagesRes2.status, 200, 'GET messages after restart should return 200');
const messagesAfter = await messagesRes2.json();
assert.equal(messagesAfter.length, 2, 'expected same 2 messages after restart');
assert.equal(messagesAfter[0].role, 'user');
assert.equal(messagesAfter[0].content, messagesBefore[0].content, 'user message content unchanged after restart');
assert.equal(messagesAfter[1].role, 'assistant');
assert.equal(messagesAfter[1].content, messagesBefore[1].content, 'assistant message content unchanged after restart');
console.log('[PASS] messages persisted across backend restart (SQLite volume)');

console.log('\n=== ALL 8 CHECKS PASSED ===');
process.exit(0);
