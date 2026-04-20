import React, { useState } from 'react';
import { Heart, LogIn } from 'lucide-react';

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await onLogin(username, password);
    } catch (err) {
      setError(err.message || 'Błąd logowania');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-gray-950">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <Heart className="w-12 h-12 text-red-500 mx-auto mb-3" />
          <h1 className="text-2xl font-bold text-white">Keyselector</h1>
          <p className="text-gray-500 text-sm mt-1">Selekcja klatek angiograficznych</p>
        </div>

        <form onSubmit={handleSubmit} className="bg-gray-900 border border-gray-800 rounded-xl p-6 space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">Nazwa użytkownika</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-blue-500"
              autoFocus
              required
            />
          </div>

          <div>
            <label className="block text-sm text-gray-400 mb-1">Hasło</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-blue-500"
              required
            />
          </div>

          {error && (
            <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/50 rounded-lg px-3 py-2">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 text-white rounded-lg font-medium transition-colors"
          >
            <LogIn className="w-4 h-4" />
            {loading ? 'Logowanie...' : 'Zaloguj się'}
          </button>
        </form>

        <p className="text-center text-gray-600 text-xs mt-4">
          Domyślne konto: admin / admin
        </p>
      </div>
    </div>
  );
}
