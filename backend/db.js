import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import Database from 'better-sqlite3';

let db;

function ensureInit() {
  if (!db) {
    throw new Error('Database not initialized. Call initDb(dbPath) first.');
  }
}

export function initDb(dbPath) {
  fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  db = new Database(dbPath);
  db.pragma('journal_mode = WAL');
  db.exec(`
    CREATE TABLE IF NOT EXISTS conversations (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      model TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS messages (
      id TEXT PRIMARY KEY,
      conversation_id TEXT NOT NULL,
      role TEXT NOT NULL,
      content TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
  `);
}

export function createConversation(model) {
  ensureInit();
  const id = randomUUID();
  const title = 'New Chat';
  const now = new Date().toISOString();
  db.prepare(
    'INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)'
  ).run(id, title, model, now, now);
  return { id, title, model, created_at: now, updated_at: now };
}

export function listConversations() {
  ensureInit();
  return db.prepare('SELECT * FROM conversations ORDER BY updated_at DESC').all();
}

export function getConversation(id) {
  ensureInit();
  return db.prepare('SELECT * FROM conversations WHERE id = ?').get(id);
}

export function listMessages(conversationId) {
  ensureInit();
  return db
    .prepare('SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC')
    .all(conversationId);
}

export function addMessage(conversationId, role, content) {
  ensureInit();
  const id = randomUUID();
  const created_at = new Date().toISOString();
  db.prepare(
    'INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)'
  ).run(id, conversationId, role, content, created_at);
  return { id, role, content, created_at };
}

export function touchConversation(id, updates = {}) {
  ensureInit();
  const fields = [];
  const values = [];
  if (updates.title !== undefined) {
    fields.push('title = ?');
    values.push(updates.title);
  }
  if (updates.model !== undefined) {
    fields.push('model = ?');
    values.push(updates.model);
  }
  fields.push('updated_at = ?');
  values.push(new Date().toISOString());
  values.push(id);
  db.prepare(`UPDATE conversations SET ${fields.join(', ')} WHERE id = ?`).run(...values);
}
