export default function Sidebar({ conversations, selectedId, onSelect, onNewChat }) {
  return (
    <div className="flex flex-col h-full w-64 bg-gray-50 border-r border-gray-200">
      <button
        onClick={onNewChat}
        className="m-3 px-4 py-2 rounded-md bg-blue-600 text-white font-medium hover:bg-blue-700 transition-colors"
      >
        + New Chat
      </button>
      <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-1">
        {conversations.map((conv) => (
          <button
            key={conv.id}
            onClick={() => onSelect(conv.id)}
            className={`w-full text-left px-3 py-2 rounded-md truncate transition-colors ${
              conv.id === selectedId
                ? 'bg-blue-600 text-white'
                : 'bg-white text-gray-800 border border-gray-200 hover:bg-gray-100'
            }`}
          >
            {conv.title}
          </button>
        ))}
      </div>
    </div>
  );
}
