import React, { useState, useEffect, useCallback } from 'react';
import { Link2, Unlink, Users } from 'lucide-react';
import {
  adminListUsers,
  adminListDatasets,
  adminListUserDatasets,
  adminAssignDataset,
  adminUnassignDataset,
} from '../api';

export default function UserDatasetAssignments() {
  const [users, setUsers] = useState([]);
  const [datasets, setDatasets] = useState([]);
  const [selectedUserId, setSelectedUserId] = useState(null);
  const [assigned, setAssigned] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const loadTop = useCallback(async () => {
    try {
      const [us, ds] = await Promise.all([adminListUsers(), adminListDatasets()]);
      setUsers(us);
      setDatasets(ds);
      if (us.length && selectedUserId == null) setSelectedUserId(us[0].id);
    } catch (err) { setError(err.message); }
  }, [selectedUserId]);

  const loadAssigned = useCallback(async () => {
    if (selectedUserId == null) return;
    try {
      setAssigned(await adminListUserDatasets(selectedUserId));
    } catch (err) { setError(err.message); }
  }, [selectedUserId]);

  useEffect(() => { loadTop(); }, [loadTop]);
  useEffect(() => { loadAssigned(); }, [loadAssigned]);

  const assign = async (datasetId) => {
    setBusy(true); setError('');
    try {
      await adminAssignDataset(selectedUserId, datasetId);
      await loadAssigned();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const unassign = async (datasetId) => {
    setBusy(true); setError('');
    try {
      await adminUnassignDataset(selectedUserId, datasetId);
      await loadAssigned();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const assignedIds = new Set(assigned.map((d) => d.id));
  const available = datasets.filter((d) => !assignedIds.has(d.id));

  return (
    <div className="space-y-4">
      {error && (
        <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/50 rounded-lg px-3 py-2">
          {error} <button onClick={() => setError('')} className="ml-2 text-red-500 hover:text-red-300">✕</button>
        </div>
      )}

      <div>
        <label className="text-xs text-gray-400 uppercase tracking-wider flex items-center gap-2 mb-2">
          <Users className="w-4 h-4" /> Użytkownik
        </label>
        <select
          value={selectedUserId ?? ''}
          onChange={(e) => setSelectedUserId(Number(e.target.value))}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
        >
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.username} ({u.role})
            </option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <h4 className="text-sm font-semibold text-gray-400 mb-2">Przypisane ({assigned.length})</h4>
          <div className="space-y-1.5">
            {assigned.length === 0 && <p className="text-xs text-gray-500">Brak przypisań.</p>}
            {assigned.map((d) => (
              <div key={d.id} className="bg-gray-800/50 rounded p-2 flex items-center justify-between gap-2">
                <span className="text-sm text-white truncate">{d.name}</span>
                <button
                  onClick={() => unassign(d.id)}
                  disabled={busy}
                  className="p-1 hover:bg-gray-700 rounded transition-colors shrink-0"
                  title="Odepnij"
                >
                  <Unlink className="w-3.5 h-3.5 text-gray-500 hover:text-red-400" />
                </button>
              </div>
            ))}
          </div>
        </div>

        <div>
          <h4 className="text-sm font-semibold text-gray-400 mb-2">Dostępne ({available.length})</h4>
          <div className="space-y-1.5">
            {available.length === 0 && <p className="text-xs text-gray-500">Wszystkie zbiory są przypisane.</p>}
            {available.map((d) => (
              <div key={d.id} className="bg-gray-800/50 rounded p-2 flex items-center justify-between gap-2">
                <span className="text-sm text-gray-300 truncate">{d.name}</span>
                <button
                  onClick={() => assign(d.id)}
                  disabled={busy}
                  className="p-1 hover:bg-gray-700 rounded transition-colors shrink-0"
                  title="Przypisz"
                >
                  <Link2 className="w-3.5 h-3.5 text-gray-500 hover:text-blue-400" />
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
