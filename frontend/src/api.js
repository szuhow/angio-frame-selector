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
// Data API (all require auth)
// ---------------------------------------------------------------------------
export async function fetchPatients() {
  const res = await apiFetch(`${API}/api/patients`);
  if (!res.ok) throw new Error('Failed to fetch patients');
  return res.json();
}

function encodePathSegments(value) {
  return value.split('/').map(encodeURIComponent).join('/');
}

export async function fetchFramesBulk(patientId, sequenceId) {
  const res = await apiFetch(`${API}/api/patients/${encodeURIComponent(patientId)}/sequences/${encodePathSegments(sequenceId)}/frames_bulk`);
  if (!res.ok) throw new Error('Failed to fetch frames');
  return res.json();
}

export function frameUrl(patientId, sequenceId, frameIdx) {
  return `${API}/api/patients/${encodeURIComponent(patientId)}/sequences/${encodePathSegments(sequenceId)}/frames/${frameIdx}`;
}

export async function submitAnnotation(payload) {
  const res = await apiFetch(`${API}/api/annotations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error('Failed to save annotation');
  return res.json();
}

export async function submitSkip(payload) {
  const res = await apiFetch(`${API}/api/skip`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error('Failed to save skip');
  return res.json();
}

export async function fetchAnnotations() {
  const res = await apiFetch(`${API}/api/annotations`);
  if (!res.ok) throw new Error('Failed to fetch annotations');
  return res.json();
}

export async function fetchSequenceAnnotation(patientId, sequenceId) {
  const res = await apiFetch(`${API}/api/annotations/${encodeURIComponent(patientId)}/${encodePathSegments(sequenceId)}`);
  if (!res.ok) throw new Error('Failed to fetch annotation');
  return res.json();
}

export async function fetchStats() {
  const res = await apiFetch(`${API}/api/stats`);
  if (!res.ok) throw new Error('Failed to fetch stats');
  return res.json();
}

export async function exportCoco() {
  const res = await apiFetch(`${API}/api/export/coco`);
  if (!res.ok) throw new Error('Failed to export COCO');
  return res.json();
}
