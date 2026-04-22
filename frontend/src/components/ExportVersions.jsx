import React, { useState, useEffect, useCallback } from 'react';
import { Tag, Download, Trash2, Plus } from 'lucide-react';
import {
  adminListDatasets,
  listExportVersions,
  createExportVersion,
  deleteExportVersion,
  exportVersionDownloadUrl,
  getToken,
} from '../api';

const FORMATS = [
  { value: 'annotations-json', label: 'annotations-json' },
  { value: 'coco', label: 'coco' },
];

export default function ExportVersions() {
  const [datasets, setDatasets] = useState([]);
  const [selectedDsId, setSelectedDsId] = useState(null);
  const [versions, setVersions] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const [newVersion, setNewVersion] = useState('');
  const [newFormat, setNewFormat] = useState('annotations-json');
  const [newNotes, setNewNotes] = useState('');

  const loadDatasets = useCallback(async () => {
    try {
      const ds = await adminListDatasets();
      setDatasets(ds);
      if (ds.length && selectedDsId == null) setSelectedDsId(ds[0].id);
    } catch (err) { setError(err.message); }
  }, [selectedDsId]);

  const loadVersions = useCallback(async () => {
    if (selectedDsId == null) return;
    try {
      setVersions(await listExportVersions(selectedDsId));
    } catch (err) { setError(err.message); }
  }, [selectedDsId]);

  useEffect(() => { loadDatasets(); }, [loadDatasets]);
  useEffect(() => { loadVersions(); }, [loadVersions]);

  const create = async (e) => {
    e.preventDefault();
    if (selectedDsId == null) return;
    setBusy(true); setError('');
    try {
      await createExportVersion({
        dataset_id: selectedDsId,
        version: newVersion,
        format: newFormat,
        notes: newNotes,
      });
      setNewVersion(''); setNewNotes('');
      await loadVersions();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const remove = async (v) => {
    if (!confirm(`Usunąć wersję "${v.version}" (${v.format})?`)) return;
    try {
      await deleteExportVersion(v.id);
      await loadVersions();
    } catch (err) { setError(err.message); }
  };

  // Download through fetch + blob so we can pass the Authorization header
  const download = async (v) => {
    try {
      const res = await fetch(exportVersionDownloadUrl(v.id), {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || 'Pobieranie nieudane');
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${v.format}-${v.version}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) { setError(err.message); }
  };

  return (
    <div className="space-y-4">
      {error && (
        <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/50 rounded-lg px-3 py-2">
          {error} <button onClick={() => setError('')} className="ml-2 text-red-500 hover:text-red-300">✕</button>
        </div>
      )}

      <div>
        <label className="text-xs text-gray-400 uppercase tracking-wider flex items-center gap-2 mb-2">
          <Tag className="w-4 h-4" /> Zbiór danych
        </label>
        <select
          value={selectedDsId ?? ''}
          onChange={(e) => setSelectedDsId(Number(e.target.value))}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
        >
          {datasets.map((d) => (
            <option key={d.id} value={d.id}>{d.name}</option>
          ))}
        </select>
      </div>

      {/* Create version form */}
      <form onSubmit={create} className="bg-gray-800/50 rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider flex items-center gap-2">
          <Plus className="w-4 h-4" /> Nowa wersja eksportu
        </h3>
        <div className="grid grid-cols-3 gap-3">
          <input
            type="text"
            placeholder="v1.0.0"
            value={newVersion}
            onChange={(e) => setNewVersion(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
            required
            pattern="[A-Za-z0-9][A-Za-z0-9._\-]{0,63}"
          />
          <select
            value={newFormat}
            onChange={(e) => setNewFormat(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
          >
            {FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
          <input
            type="text"
            placeholder="Notatki (opcjonalnie)"
            value={newNotes}
            onChange={(e) => setNewNotes(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
          />
        </div>
        <button type="submit" disabled={busy || selectedDsId == null} className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg font-medium transition-colors">
          Utwórz wersję
        </button>
      </form>

      {/* Versions list */}
      <div className="space-y-2">
        {versions.length === 0 && (
          <p className="text-sm text-gray-500 px-1">Brak wersji dla tego zbioru.</p>
        )}
        {versions.map((v) => (
          <div key={v.id} className="bg-gray-800/50 rounded-lg p-3 flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-white font-medium text-sm">{v.version}</span>
                <span className="text-xs px-2 py-0.5 rounded border bg-gray-800 text-gray-400 border-gray-700">{v.format}</span>
              </div>
              <div className="text-xs text-gray-500 font-mono truncate">sha256: {v.sha256}</div>
              <div className="text-xs text-gray-500">
                {v.size_bytes} B · {new Date(v.created_at).toLocaleString()}
                {v.notes && <> · <span className="text-gray-400">{v.notes}</span></>}
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={() => download(v)}
                className="p-1 hover:bg-gray-700 rounded transition-colors"
                title="Pobierz"
              >
                <Download className="w-4 h-4 text-gray-500 hover:text-green-400" />
              </button>
              <button
                onClick={() => remove(v)}
                className="p-1 hover:bg-gray-700 rounded transition-colors"
                title="Usuń wersję"
              >
                <Trash2 className="w-4 h-4 text-gray-500 hover:text-red-400" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
