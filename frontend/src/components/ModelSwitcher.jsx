import { useState, useEffect } from 'react';
import { listModels } from '../api.js';

export default function ModelSwitcher({ model, onChange }) {
  const [models, setModels] = useState([]);

  useEffect(() => {
    listModels().then(setModels);
  }, []);

  return (
    <select
      value={model}
      onChange={(e) => onChange(e.target.value)}
      className="border rounded px-2 py-1 bg-white"
    >
      {models.map((m) => (
        <option key={m.id} value={m.id}>
          {m.label}
        </option>
      ))}
    </select>
  );
}
