import React, { useState, useEffect, useCallback } from 'react';
import { Database, Plus, Trash2, Upload, Folder, RefreshCw } from 'lucide-react';
import {
  adminListDatasets,
  adminListLibrary,
  adminRegisterDataset,
  adminUploadDataset,
  adminDeleteDataset,
} from '../api';

export default function DatasetManager({ onChange }) {
  const [datasets, setDatasets] = useState([]);
  const [library, setLibrary] = useState({ library_root: '', entries: [] });
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [busy, setBusy] = useState(false);

  // Register-existing form
  const [regName, setRegName] = useState('');
  const [regSource, setRegSource] = useState('');

  // Upload form
  const [upName, setUpName] = useState('');
  const [upFile, setUpFile] = useState(null);
  const [upProgress, setUpProgress] = useState(0);

  const load = useCallback(async () => {
    try {
      const [ds, lib] = await Promise.all([adminListDatasets(), adminListLibrary()]);
      setDatasets(ds);
      setLibrary(lib);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleRegister = async (e) => {
    e.preventDefault();
    setError(''); setSuccess(''); setBusy(true);
    try {
      await adminRegisterDataset({ name: regName, source_path: regSource });
      setSuccess(`Zarejestrowano zbiór "${regName}"`);
      setRegName(''); setRegSource('');
      await load();
      onChange && onChange();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const handleUpload = async (e) => {
    e.preventDefault();
    if (!upFile) { setError('Wybierz plik ZIP'); return; }
    setError(''); setSuccess(''); setBusy(true); setUpProgress(0);
    try {
      await adminUploadDataset({
        name: upName,
        file: upFile,
        onProgress: (p) => setUpProgress(Math.round(p * 100)),
      });
      setSuccess(`Wgrano zbiór "${upName}"`);
      setUpName(''); setUpFile(null); setUpProgress(0);
      await load();
      onChange && onChange();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const handleDelete = async (ds) => {
    if (!confirm(`Usunąć zbiór "${ds.name}"? Wszystkie adnotacje i wersje eksportu zostaną również usunięte.`)) return;
    try {
      await adminDeleteDataset(ds.id);
      await load();
      onChange && onChange();
    } catch (err) { setError(err.message); }
  };

  const unregistered = (library.entries || []).filter((e) => !e.registered && e.has_data);

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

      {/* Register existing library folder */}
      <form onSubmit={handleRegister} className="bg-gray-800/50 rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-2">
          <Folder className="w-4 h-4" /> Zarejestruj istniejący katalog
        </h3>
        <p className="text-xs text-gray-500">
          Katalog biblioteki na serwerze: <code className="text-gray-400">{library.library_root}</code>
        </p>
        <div className="grid grid-cols-2 gap-3">
          <input
            type="text"
            placeholder="Nazwa zbioru"
            value={regName}
            onChange={(e) => setRegName(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
            required
          />
          <select
            value={regSource}
            onChange={(e) => setRegSource(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
            required
          >
            <option value="">— wybierz katalog —</option>
            {unregistered.map((e) => (
              <option key={e.source_path} value={e.source_path}>{e.name}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <button type="submit" disabled={busy} className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg font-medium transition-colors">
            Zarejestruj
          </button>
          <button type="button" onClick={load} className="p-2 hover:bg-gray-700 rounded-lg transition-colors" title="Odśwież">
            <RefreshCw className="w-4 h-4 text-gray-400" />
          </button>
          <span className="text-xs text-gray-500">{unregistered.length} nowych katalogów</span>
        </div>
      </form>

      {/* Upload ZIP */}
      <form onSubmit={handleUpload} className="bg-gray-800/50 rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-2">
          <Upload className="w-4 h-4" /> Wgraj archiwum ZIP
        </h3>
        <div className="grid grid-cols-2 gap-3">
          <input
            type="text"
            placeholder="Nazwa zbioru"
            value={upName}
            onChange={(e) => setUpName(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
            required
          />
          <input
            type="file"
            accept=".zip,application/zip"
            onChange={(e) => setUpFile(e.target.files?.[0] || null)}
            className="text-sm text-gray-300 file:bg-gray-700 file:text-gray-200 file:border-0 file:px-3 file:py-1.5 file:rounded file:mr-3"
            required
          />
        </div>
        {upProgress > 0 && upProgress < 100 && (
          <div className="w-full h-1.5 bg-gray-800 rounded-full overflow-hidden">
            <div className="h-full bg-blue-500 transition-all" style={{ width: `${upProgress}%` }} />
          </div>
        )}
        <button type="submit" disabled={busy} className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg font-medium transition-colors">
          Wgraj zbiór
        </button>
        <p className="text-xs text-gray-500">Akceptowane wpisy w archiwum: <code>.dcm</code>, <code>.png</code>. Ochrona zip-slip aktywna.</p>
      </form>

      {/* List */}
      <div>
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-2 mb-3">
          <Database className="w-4 h-4" /> Zbiory danych ({datasets.length})
        </h3>
        <div className="space-y-2">
          {datasets.length === 0 && (
            <p className="text-sm text-gray-500 px-1">Brak zbiorów. Utwórz pierwszy powyżej.</p>
          )}
          {datasets.map((d) => (
            <div key={d.id} className="bg-gray-800/50 rounded-lg p-3 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-white font-medium text-sm truncate">{d.name}</span>
                  <code className="text-xs text-gray-500">{d.slug}</code>
                </div>
                <div className="text-xs text-gray-500 truncate">{d.root_path}</div>
              </div>
              <button
                onClick={() => handleDelete(d)}
                className="p-1 hover:bg-gray-700 rounded transition-colors shrink-0"
                title="Usuń zbiór"
              >
                <Trash2 className="w-4 h-4 text-gray-500 hover:text-red-400" />
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
