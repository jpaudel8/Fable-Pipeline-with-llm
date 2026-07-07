const API_BASE = 'http://localhost:3001';

export async function listModels() {
  return fetch(`${API_BASE}/api/models`).then(r => r.json());
}

export async function listConversations() {
  return fetch(`${API_BASE}/api/conversations`).then(r => r.json());
}

export async function createConversation(model) {
  return fetch(`${API_BASE}/api/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model })
  }).then(r => r.json());
}

export async function listMessages(conversationId) {
  return fetch(`${API_BASE}/api/conversations/${conversationId}/messages`).then(r => r.json());
}

export function streamMessage(conversationId, content, model, { onDelta, onDone, onError }) {
  (async () => {
    try {
      const res = await fetch(`${API_BASE}/api/conversations/${conversationId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, model })
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let idx;
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const rawEvent = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);

          const dataLine = rawEvent.split('\n').find(l => l.startsWith('data: '));
          if (!dataLine) continue;

          let ev;
          try {
            ev = JSON.parse(dataLine.slice('data: '.length));
          } catch {
            continue;
          }

          if (ev.delta !== undefined) onDelta(ev.delta);
          if (ev.done === true) onDone(ev.message);
          if (ev.error) onError(ev.error);
        }
      }
    } catch (err) {
      onError(err.message);
    }
  })();
}
