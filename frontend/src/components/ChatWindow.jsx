import { useState, useEffect } from 'react';
import { listMessages, streamMessage } from '../api.js';

export default function ChatWindow({ conversationId, model, onMessageSent }) {
  const [messages, setMessages] = useState([]);
  const [streamingText, setStreamingText] = useState('');
  const [inputValue, setInputValue] = useState('');

  useEffect(() => {
    if (conversationId) {
      listMessages(conversationId).then(setMessages);
    } else {
      setMessages([]);
    }
    setStreamingText('');
  }, [conversationId]);

  function handleSend() {
    if (!conversationId || !inputValue.trim()) return;
    const text = inputValue.trim();
    setInputValue('');
    setMessages((prev) => [
      ...prev,
      {
        id: 'local-' + Date.now(),
        role: 'user',
        content: text,
        created_at: new Date().toISOString(),
      },
    ]);
    streamMessage(conversationId, text, model, {
      onDelta: (t) => setStreamingText((prev) => prev + t),
      onDone: (message) => {
        setMessages((prev) => [...prev, message]);
        setStreamingText('');
        onMessageSent && onMessageSent();
      },
      onError: (msg) => {
        setStreamingText('');
        setMessages((prev) => [
          ...prev,
          {
            id: 'err-' + Date.now(),
            role: 'assistant',
            content: 'Error: ' + msg,
            created_at: new Date().toISOString(),
          },
        ]);
      },
    });
  }

  if (!conversationId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        Select or start a new chat
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        {messages.map((m) => (
          <div
            key={m.id}
            className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}
          >
            <div
              className={
                m.role === 'user'
                  ? 'bg-blue-600 text-white rounded-2xl px-4 py-2 max-w-[70%]'
                  : 'bg-gray-100 text-gray-900 rounded-2xl px-4 py-2 max-w-[70%]'
              }
            >
              {m.content}
            </div>
          </div>
        ))}
        {streamingText && (
          <div className="flex justify-start">
            <div className="bg-gray-100 text-gray-900 rounded-2xl px-4 py-2 max-w-[70%]">
              {streamingText}
            </div>
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 p-3 border-t">
        <input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSend();
          }}
          className="flex-1 border rounded px-3 py-2"
        />
        <button
          onClick={handleSend}
          className="bg-blue-600 text-white rounded px-4 py-2"
        >
          Send
        </button>
      </div>
    </div>
  );
}
