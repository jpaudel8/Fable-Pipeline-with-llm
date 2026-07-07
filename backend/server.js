import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import { initDb, createConversation, listConversations, getConversation, listMessages, addMessage, touchConversation } from './db.js';
import { GROQ_MODELS, streamGroqChat } from './groq.js';

initDb(process.env.DB_PATH || './data/chat.db');

const app = express();
app.use(cors({ origin: process.env.CORS_ORIGIN || 'http://localhost:5173' }));
app.use(express.json());

app.get('/api/health', (req, res) => {
  res.status(200).json({ status: 'ok' });
});

app.get('/api/models', (req, res) => {
  res.status(200).json(GROQ_MODELS);
});

app.get('/api/conversations', (req, res) => {
  res.status(200).json(listConversations());
});

app.post('/api/conversations', (req, res) => {
  const { model } = req.body;
  res.status(201).json(createConversation(model || GROQ_MODELS[0].id));
});

app.get('/api/conversations/:id/messages', (req, res) => {
  const { id } = req.params;
  if (!getConversation(id)) {
    return res.status(404).json({ error: 'not found' });
  }
  res.status(200).json(listMessages(id));
});

app.post('/api/conversations/:id/messages', async (req, res) => {
  const { id } = req.params;
  const { content, model } = req.body;

  if (!getConversation(id)) {
    return res.status(404).json({ error: 'not found' });
  }
  if (!content) {
    return res.status(400).json({ error: 'content is required' });
  }

  addMessage(id, 'user', content);
  touchConversation(id, listMessages(id).length === 1 ? { title: content.slice(0, 40).trim() } : {});

  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
  });

  const history = listMessages(id).map((m) => ({ role: m.role, content: m.content }));
  const useModel = model || getConversation(id).model;
  let fullText = '';
  try {
    for await (const delta of streamGroqChat(useModel, history)) {
      fullText += delta;
      res.write(`data: ${JSON.stringify({ delta })}\n\n`);
    }
    const saved = addMessage(id, 'assistant', fullText);
    touchConversation(id, {});
    res.write(`data: ${JSON.stringify({ done: true, message: saved })}\n\n`);
  } catch (err) {
    res.write(`data: ${JSON.stringify({ error: err.message })}\n\n`);
  }
  res.end();
});

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
  console.log(`Server listening on port ${PORT}`);
});
