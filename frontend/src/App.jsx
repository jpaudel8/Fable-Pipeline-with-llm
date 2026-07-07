import { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import ModelSwitcher from './components/ModelSwitcher';
import ChatWindow from './components/ChatWindow';
import { listConversations, createConversation } from './api';

export default function App() {
  const [conversations, setConversations] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [model, setModel] = useState('llama-3.3-70b-versatile');

  const refreshConversations = async () => setConversations(await listConversations());

  useEffect(() => {
    refreshConversations();
  }, []);

  const handleNewChat = async () => {
    const conv = await createConversation(model);
    await refreshConversations();
    setSelectedId(conv.id);
  };

  return (
    <div className="flex h-screen bg-white text-gray-900">
      <Sidebar conversations={conversations} selectedId={selectedId} onSelect={setSelectedId} onNewChat={handleNewChat} />
      <main className="flex-1 flex flex-col">
        <div className="border-b p-2">
          <ModelSwitcher model={model} onChange={setModel} />
        </div>
        <ChatWindow conversationId={selectedId} model={model} onMessageSent={refreshConversations} />
      </main>
    </div>
  );
}
