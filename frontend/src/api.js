const API = '';

// ---------------------------------------------------------------------------
// Token management
// ---------------------------------------------------------------------------
let _token = localStorage.getItem('ks_token');

export function setToken(token) {
  _token = token;
  if (token) localStorage.setItem('ks_token', token);
  else localStorage.removeItem('ks_token');
}

export function getToken() {
  return _token;
}

function authHeaders(extra = {}) {
  const headers = { ...extra };
  if (_token) headers['Authorization'] = `Bearer ${_token}`;
  return headers;
}

async function apiFetch(url, options = {}) {
  const headers = authHeaders(options.headers || {});
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) {
    setToken(null);
    window.dispatchEvent(new Event('auth-expired'));
    throw new Error('Sesja wygasła');
  }
  return res;
}

async function parseError(res, fallback) {
  try {
    const data = await res.json();
    return new Error(data.detail || fallback);
  } catch {
    return new Error(fallback);
  }
}

function encodePathSegments(value) {
  return value.split('/').map(encodeURIComponent).join('/');
}

function qs(params) {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '') s.set(k, String(v));
  }
  const out = s.toString();
  return out ? `?${out}` : '';
}

// ---------------------------------------------------------------------------
// Auth API
// ---------------------------------------------------------------------------
export async function login(username, password) {
  const res = await fetch(`${API}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || 'Błąd logowania');
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Datasets (user-facing)
// ---------------------------------------------------------------------------
export async function fetchDatasets() {
  const res = await apiFetch(`${API}/api/datasets`);
  if (!res.ok) throw await parseError(res, 'Nie udało się pobrać zbiorów');
  return res.json();
}

// ---------------------------------------------------------------------------
// Data API
// ---------------------------------------------------------------------------
export async function fetchPatients(datasetId) {
  const res = await apiFetch(`${API}/api/patients${qs({ dataset_id: datasetId })}`);
  if (!res.ok) throw await parseError(res, 'Failed to fetch patients');
  return res.json();
}

export async function fetchFramesBulk(datasetId, patientId, sequenceId) {
  const url = `${API}/api/patients/${encodeURIComponent(patientId)}/sequences/${encodePathSegments(sequenceId)}/frames_bulk${qs({ dataset_id: datasetId })}`;
  const res = await apiFetch(url);
  if (!res.ok) throw await parseError(res, 'Failed to fetch frames');
  return res.json();
}

export function frameUrl(datasetId, patientId, sequenceId, frameIdx) {
  return `${API}/api/patients/${encodeURIComponent(patientId)}/sequences/${encodePathSegments(sequenceId)}/frames/${frameIdx}${qs({ dataset_id: datasetId })}`;
}

export async function submitAnnotation(payload) {
  const res = await apiFetch(`${API}/api/annotations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw await parseError(res, 'Failed to save annotation');
  return res.json();
}

export async function submitSkip(payload) {
  const res = await apiFetch(`${API}/api/skip`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw await parseError(res, 'Failed to save skip');
  return res.json();
}

export async function fetchAnnotations(datasetId) {
  const res = await apiFetch(`${API}/api/annotations${qs({ dataset_id: datasetId })}`);
  if (!res.ok) throw await parseError(res, 'Failed to fetch annotations');
  return res.json();
}

export async function fetchSequenceAnnotation(datasetId, patientId, sequenceId) {
  const url = `${API}/api/annotations/${encodeURIComponent(patientId)}/${encodePathSegments(sequenceId)}${qs({ dataset_id: datasetId })}`;
  const res = await apiFetch(url);
  if (!res.ok) throw await parseError(res, 'Failed to fetch annotation');
  return res.json();
}

export async function fetchStats(datasetId) {
  const res = await apiFetch(`${API}/api/stats${qs({ dataset_id: datasetId })}`);
  if (!res.ok) throw await parseError(res, 'Failed to fetch stats');
  return res.json();
}

export async function exportCoco(datasetId) {
  const res = await apiFetch(`${API}/api/export/coco${qs({ dataset_id: datasetId })}`);
  if (!res.ok) throw await parseError(res, 'Failed to export COCO');
  return res.json();
}

// ---------------------------------------------------------------------------
// Versioned exports
// ---------------------------------------------------------------------------
export async function listExportVersions(datasetId) {
  const res = await apiFetch(`${API}/api/export/versions${qs({ dataset_id: datasetId })}`);
  if (!res.ok) throw await parseError(res, 'Nie udało się pobrać wersji eksportu');
  return res.json();
}

export async function createExportVersion({ dataset_id, version, format, notes = '' }) {
  const res = await apiFetch(`${API}/api/export/versions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset_id, version, format, notes }),
  });
  if (!res.ok) throw await parseError(res, 'Nie udało się utworzyć wersji');
  return res.json();
}

export function exportVersionDownloadUrl(versionId) {
  return `${API}/api/export/versions/${versionId}/download`;
}

export async function deleteExportVersion(versionId) {
  const res = await apiFetch(`${API}/api/export/versions/${versionId}`, { method: 'DELETE' });
  if (!res.ok) throw await parseError(res, 'Nie udało się usunąć wersji');
  return res.json();
}

// ---------------------------------------------------------------------------
// Admin – users
// ---------------------------------------------------------------------------
export async function adminListUsers() {
  const res = await apiFetch(`${API}/api/admin/users`);
  if (!res.ok) throw await parseError(res, 'Nie udało się pobrać użytkowników');
  return res.json();
}

export async function adminCreateUser({ username, password, role }) {
  const res = await apiFetch(`${API}/api/admin/users`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, role }),
  });
  if (!res.ok) throw await parseError(res, 'Nie udało się utworzyć użytkownika');
  return res.json();
}

export async function adminDeleteUser(userId) {
  const res = await apiFetch(`${API}/api/admin/users/${userId}`, { method: 'DELETE' });
  if (!res.ok) throw await parseError(res, 'Nie udało się usunąć użytkownika');
  return res.json();
}

export async function adminRegenerateToken(userId) {
  const res = await apiFetch(`${API}/api/admin/users/${userId}/regenerate-token`, { method: 'POST' });
  if (!res.ok) throw await parseError(res, 'Nie udało się wygenerować tokenu');
  return res.json();
}

// ---------------------------------------------------------------------------
// Admin – datasets
// ---------------------------------------------------------------------------
export async function adminListDatasets() {
  const res = await apiFetch(`${API}/api/admin/datasets`);
  if (!res.ok) throw await parseError(res, 'Nie udało się pobrać datasetów');
  return res.json();
}

export async function adminListLibrary() {
  const res = await apiFetch(`${API}/api/admin/datasets/library`);
  if (!res.ok) throw await parseError(res, 'Nie udało się odczytać biblioteki');
  return res.json();
}

export async function adminRegisterDataset({ name, source_path }) {
  const form = new FormData();
  form.append('name', name);
  form.append('source_path', source_path);
  const res = await apiFetch(`${API}/api/admin/datasets`, { method: 'POST', body: form });
  if (!res.ok) throw await parseError(res, 'Nie udało się zarejestrować zbioru');
  return res.json();
}

export async function adminUploadDataset({ name, file, onProgress }) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API}/api/admin/datasets`);
    if (_token) xhr.setRequestHeader('Authorization', `Bearer ${_token}`);
    xhr.upload.onprogress = (e) => {
      if (onProgress && e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)); } catch { resolve({}); }
      } else {
        let msg = 'Błąd wysyłki';
        try { msg = JSON.parse(xhr.responseText).detail || msg; } catch { /* ignore */ }
        reject(new Error(msg));
      }
    };
    xhr.onerror = () => reject(new Error('Błąd sieci podczas wysyłki'));
    const form = new FormData();
    form.append('name', name);
    form.append('file', file);
    xhr.send(form);
  });
}

export async function adminDeleteDataset(datasetId) {
  const res = await apiFetch(`${API}/api/admin/datasets/${datasetId}`, { method: 'DELETE' });
  if (!res.ok) throw await parseError(res, 'Nie udało się usunąć zbioru');
  return res.json();
}

export async function adminListDatasetUsers(datasetId) {
  const res = await apiFetch(`${API}/api/admin/datasets/${datasetId}/users`);
  if (!res.ok) throw await parseError(res, 'Nie udało się pobrać użytkowników zbioru');
  return res.json();
}

export async function adminListUserDatasets(userId) {
  const res = await apiFetch(`${API}/api/admin/users/${userId}/datasets`);
  if (!res.ok) throw await parseError(res, 'Nie udało się pobrać zbiorów użytkownika');
  return res.json();
}

export async function adminAssignDataset(userId, datasetId) {
  const res = await apiFetch(`${API}/api/admin/users/${userId}/datasets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset_id: datasetId }),
  });
  if (!res.ok) throw await parseError(res, 'Nie udało się przypisać zbioru');
  return res.json();
}

export async function adminUnassignDataset(userId, datasetId) {
  const res = await apiFetch(`${API}/api/admin/users/${userId}/datasets/${datasetId}`, { method: 'DELETE' });
  if (!res.ok) throw await parseError(res, 'Nie udało się odpiąć zbioru');
  return res.json();
}

// ---------------------------------------------------------------------------
// Sequence metadata (DICOM tags / DICOM-JSON sidecar)
// ---------------------------------------------------------------------------
export async function fetchSequenceMetadata(datasetId, patientId, sequenceId) {
  const url = `${API}/api/patients/${encodeURIComponent(patientId)}/sequences/${encodePathSegments(sequenceId)}/metadata${qs({ dataset_id: datasetId })}`;
  const res = await apiFetch(url);
  if (!res.ok) throw await parseError(res, 'Nie udało się pobrać metadanych');
  return res.json();
}

export async function fetchMetadataConfig() {
  const res = await apiFetch(`${API}/api/metadata/config`);
  if (!res.ok) throw await parseError(res, 'Nie udało się pobrać konfiguracji metadanych');
  return res.json();
}

export async function updateMetadataConfig(fields) {
  const res = await apiFetch(`${API}/api/metadata/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fields }),
  });
  if (!res.ok) throw await parseError(res, 'Nie udało się zapisać konfiguracji metadanych');
  return res.json();
}
