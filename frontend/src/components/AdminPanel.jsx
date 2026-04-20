import React, { useState, useEffect } from 'react';
import { Users, Plus, Trash2, Key, Copy, Shield, X } from 'lucide-react';

const API = '';

const ROLE_LABELS = { admin: 'Administrator', annotator: 'Adnotator', viewer: 'Odczyt API' };
const ROLE_COLORS = {
  admin: 'bg-red-900/30 text-red-400 border-red-800/50',
  annotator: 'bg-blue-900/30 text-blue-400 border-blue-800/50',
  viewer: 'bg-gray-800 text-gray-400 border-gray-700',
};

export default function AdminPanel({ token, onClose }) {
  const [users, setUsers] = useState([]);
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState('annotator');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [copiedToken, setCopiedToken] = useState(null);

  const headers = {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  };

  const loadUsers = async () => {
    try {
      const res = await fetch(`${API}/api/admin/users`, { headers });
      if (res.ok) setUsers(await res.json());
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    loadUsers();
  }, []);

  const handleCreate = async (e) => {
    e.preventDefault();
    setError('');
    setSuccess('');
    try {
      const res = await fetch(`${API}/api/admin/users`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ username: newUsername, password: newPassword, role: newRole }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Błąd');
      setSuccess(`Utworzono użytkownika "${newUsername}"`);
      setNewUsername('');
      setNewPassword('');
      loadUsers();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleDelete = async (userId, username) => {
    if (!confirm(`Usunąć użytkownika "${username}"?`)) return;
    try {
      const res = await fetch(`${API}/api/admin/users/${userId}`, { method: 'DELETE', headers });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Błąd');
      loadUsers();
    } catch (err) {
      setError(err.message);
    }
  };

  const handleRegenToken = async (userId) => {
    try {
      const res = await fetch(`${API}/api/admin/users/${userId}/regenerate-token`, {
        method: 'POST',
        headers,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Błąd');
      loadUsers();
      setSuccess('Token API został wygenerowany ponownie');
    } catch (err) {
      setError(err.message);
    }
  };

  const copyToken = (apiToken) => {
    navigator.clipboard.writeText(apiToken);
    setCopiedToken(apiToken);
    setTimeout(() => setCopiedToken(null), 2000);
  };

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800">
          <div className="flex items-center gap-2">
            <Shield className="w-5 h-5 text-blue-400" />
            <h2 className="text-lg font-semibold text-white">Zarządzanie użytkownikami</h2>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-gray-800 rounded-lg transition-colors">
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        <div className="p-6 space-y-6">
          {/* Messages */}
          {error && (
            <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/50 rounded-lg px-3 py-2">
              {error}
              <button onClick={() => setError('')} className="ml-2 text-red-500 hover:text-red-300">✕</button>
            </div>
          )}
          {success && (
            <div className="text-green-400 text-sm bg-green-900/20 border border-green-800/50 rounded-lg px-3 py-2">
              {success}
              <button onClick={() => setSuccess('')} className="ml-2 text-green-500 hover:text-green-300">✕</button>
            </div>
          )}

          {/* Create user form */}
          <form onSubmit={handleCreate} className="bg-gray-800/50 rounded-lg p-4 space-y-3">
            <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-2">
              <Plus className="w-4 h-4" /> Nowy użytkownik
            </h3>
            <div className="grid grid-cols-3 gap-3">
              <input
                type="text"
                placeholder="Login"
                value={newUsername}
                onChange={(e) => setNewUsername(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                required
              />
              <input
                type="password"
                placeholder="Hasło"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
                required
              />
              <select
                value={newRole}
                onChange={(e) => setNewRole(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
              >
                <option value="annotator">Adnotator</option>
                <option value="viewer">Odczyt API</option>
                <option value="admin">Administrator</option>
              </select>
            </div>
            <button
              type="submit"
              className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg font-medium transition-colors"
            >
              Utwórz konto
            </button>
          </form>

          {/* Users list */}
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
                        <code className="text-xs text-gray-500 font-mono max-w-[120px] truncate hidden sm:inline">
                          {u.api_token}
                        </code>
                        <button
                          onClick={() => copyToken(u.api_token)}
                          className="p-1 hover:bg-gray-700 rounded transition-colors"
                          title="Kopiuj token"
                        >
                          <Copy className={`w-3.5 h-3.5 ${copiedToken === u.api_token ? 'text-green-400' : 'text-gray-500'}`} />
                        </button>
                        <button
                          onClick={() => handleRegenToken(u.id)}
                          className="p-1 hover:bg-gray-700 rounded transition-colors"
                          title="Wygeneruj nowy token"
                        >
                          <Key className="w-3.5 h-3.5 text-gray-500 hover:text-yellow-400" />
                        </button>
                      </div>
                    )}
                    <button
                      onClick={() => handleDelete(u.id, u.username)}
                      className="p-1 hover:bg-gray-700 rounded transition-colors ml-2"
                      title="Usuń użytkownika"
                    >
                      <Trash2 className="w-3.5 h-3.5 text-gray-500 hover:text-red-400" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* API usage info */}
          <div className="bg-gray-800/30 rounded-lg p-4 border border-gray-800">
            <h3 className="text-sm font-semibold text-gray-400 mb-2">Korzystanie z API eksportu</h3>
            <code className="text-xs text-green-400 block bg-gray-950 rounded p-2 font-mono break-all">
              curl -H &quot;Authorization: Bearer ks_...&quot; http://localhost:8000/api/export/annotations
            </code>
            <p className="text-xs text-gray-500 mt-2">
              Użyj tokenu API użytkownika z dowolną rolą. Token jest widoczny powyżej przy każdym użytkowniku.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
