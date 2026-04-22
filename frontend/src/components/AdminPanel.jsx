import React, { useState, useEffect } from 'react';
import { Users, Plus, Trash2, Key, Copy, Shield, X, Database, Link2, Tag } from 'lucide-react';
import {
  adminListUsers,
  adminCreateUser,
  adminDeleteUser,
  adminRegenerateToken,
} from '../api';
import DatasetManager from './DatasetManager';
import UserDatasetAssignments from './UserDatasetAssignments';
import ExportVersions from './ExportVersions';

const ROLE_LABELS = { admin: 'Administrator', annotator: 'Adnotator', viewer: 'Odczyt API' };
const ROLE_COLORS = {
  admin: 'bg-red-900/30 text-red-400 border-red-800/50',
  annotator: 'bg-blue-900/30 text-blue-400 border-blue-800/50',
  viewer: 'bg-gray-800 text-gray-400 border-gray-700',
};

const TABS = [
  { id: 'users', label: 'Użytkownicy', icon: Users },
  { id: 'datasets', label: 'Zbiory danych', icon: Database },
  { id: 'assignments', label: 'Przypisania', icon: Link2 },
  { id: 'exports', label: 'Wersje eksportu', icon: Tag },
];

function UsersTab() {
  const [users, setUsers] = useState([]);
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState('annotator');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [copiedToken, setCopiedToken] = useState(null);

  const load = async () => {
    try { setUsers(await adminListUsers()); } catch (err) { setError(err.message); }
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async (e) => {
    e.preventDefault();
    setError(''); setSuccess('');
    try {
      await adminCreateUser({ username: newUsername, password: newPassword, role: newRole });
      setSuccess(`Utworzono użytkownika "${newUsername}"`);
      setNewUsername(''); setNewPassword('');
      await load();
    } catch (err) { setError(err.message); }
  };

  const handleDelete = async (userId, username) => {
    if (!confirm(`Usunąć użytkownika "${username}"?`)) return;
    try { await adminDeleteUser(userId); await load(); } catch (err) { setError(err.message); }
  };

  const handleRegenToken = async (userId) => {
    try {
      await adminRegenerateToken(userId);
      setSuccess('Token API został wygenerowany ponownie');
      await load();
    } catch (err) { setError(err.message); }
  };

  const copyToken = (apiToken) => {
    navigator.clipboard.writeText(apiToken);
    setCopiedToken(apiToken);
    setTimeout(() => setCopiedToken(null), 2000);
  };

  return (
    <div className="space-y-6">
      {error && (
        <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/50 rounded-lg px-3 py-2">
          {error} <button onClick={() => setError('')} className="ml-2 text-red-500 hover:text-red-300">✕</button>
        </div>
      )}
      {success && (
        <div className="text-green-400 text-sm bg-green-900/20 border border-green-800/50 rounded-lg px-3 py-2">
          {success} <button onClick={() => setSuccess('')} className="ml-2 text-green-500 hover:text-green-300">✕</button>
        </div>
      )}

      <form onSubmit={handleCreate} className="bg-gray-800/50 rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-2">
          <Plus className="w-4 h-4" /> Nowy użytkownik
        </h3>
        <div className="grid grid-cols-3 gap-3">
          <input type="text" placeholder="Login" value={newUsername} onChange={(e) => setNewUsername(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" required />
          <input type="password" placeholder="Hasło" value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500" required />
          <select value={newRole} onChange={(e) => setNewRole(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500">
            <option value="annotator">Adnotator</option>
            <option value="viewer">Odczyt API</option>
            <option value="admin">Administrator</option>
          </select>
        </div>
        <button type="submit" className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg font-medium transition-colors">
          Utwórz konto
        </button>
      </form>

      <div>
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-2 mb-3">
          <Users className="w-4 h-4" /> Użytkownicy ({users.length})
        </h3>
        <div className="space-y-2">
          {users.map((u) => (
            <div key={u.id} className="bg-gray-800/50 rounded-lg p-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <span className="text-white font-medium text-sm">{u.username}</span>
                <span className={`text-xs px-2 py-0.5 rounded border shrink-0 ${ROLE_COLORS[u.role]}`}>
                  {ROLE_LABELS[u.role]}
                </span>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {u.api_token && (
                  <div className="flex items-center gap-1">
                    <code className="text-xs text-gray-500 font-mono max-w-[120px] truncate hidden sm:inline">{u.api_token}</code>
                    <button onClick={() => copyToken(u.api_token)} className="p-1 hover:bg-gray-700 rounded transition-colors" title="Kopiuj token">
                      <Copy className={`w-3.5 h-3.5 ${copiedToken === u.api_token ? 'text-green-400' : 'text-gray-500'}`} />
                    </button>
                    <button onClick={() => handleRegenToken(u.id)} className="p-1 hover:bg-gray-700 rounded transition-colors" title="Wygeneruj nowy token">
                      <Key className="w-3.5 h-3.5 text-gray-500 hover:text-yellow-400" />
                    </button>
                  </div>
                )}
                <button onClick={() => handleDelete(u.id, u.username)} className="p-1 hover:bg-gray-700 rounded transition-colors ml-2" title="Usuń użytkownika">
                  <Trash2 className="w-3.5 h-3.5 text-gray-500 hover:text-red-400" />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function AdminPanel({ onClose, onDatasetsChanged }) {
  const [tab, setTab] = useState('users');

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-3xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
          <div className="flex items-center gap-2">
            <Shield className="w-5 h-5 text-blue-400" />
            <h2 className="text-lg font-semibold text-white">Panel administratora</h2>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-gray-800 rounded-lg transition-colors">
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        <div className="flex border-b border-gray-800 px-4">
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`flex items-center gap-2 px-4 py-3 text-sm transition-colors border-b-2 -mb-px ${
                  active
                    ? 'border-blue-500 text-white'
                    : 'border-transparent text-gray-400 hover:text-gray-200'
                }`}
              >
                <Icon className="w-4 h-4" /> {t.label}
              </button>
            );
          })}
        </div>

        <div className="p-6 overflow-y-auto">
          {tab === 'users' && <UsersTab />}
          {tab === 'datasets' && <DatasetManager onChange={onDatasetsChanged} />}
          {tab === 'assignments' && <UserDatasetAssignments />}
          {tab === 'exports' && <ExportVersions />}
        </div>
      </div>
    </div>
  );
}
