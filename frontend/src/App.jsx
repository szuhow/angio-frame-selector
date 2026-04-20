import React, { useState, useEffect, useCallback, useRef } from 'react';
import Sidebar from './components/Sidebar';
import FrameViewer from './components/FrameViewer';
import ActionPanel from './components/ActionPanel';
import StatsBar from './components/StatsBar';
import LoginPage from './components/LoginPage';
import AdminPanel from './components/AdminPanel';
import {
  setToken, getToken,
  fetchPatients, fetchFramesBulk, fetchSequenceAnnotation,
  submitAnnotation, submitSkip, login as apiLogin, exportCoco,
} from './api';
import { Heart, LogOut, Shield, Download } from 'lucide-react';

const SIDEBAR_MIN = 200;
const SIDEBAR_MAX = 480;
const SIDEBAR_DEFAULT = 288;

function loadSidebarState() {
  try {
    const raw = localStorage.getItem('ks_sidebar');
    if (raw) {
      const parsed = JSON.parse(raw);
      return {
        width: Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, parsed.width ?? SIDEBAR_DEFAULT)),
        collapsed: !!parsed.collapsed,
      };
    }
  } catch {}
  return { width: SIDEBAR_DEFAULT, collapsed: false };
}

function saveSidebarState(width, collapsed) {
  localStorage.setItem('ks_sidebar', JSON.stringify({ width, collapsed }));
}

export default function App() {
  // Auth state
  const [user, setUser] = useState(null);
  const [authChecking, setAuthChecking] = useState(true);
  const [showAdmin, setShowAdmin] = useState(false);

  // Sidebar state
  const [sidebarWidth, setSidebarWidth] = useState(() => loadSidebarState().width);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => loadSidebarState().collapsed);
  const isResizing = useRef(false);

  // App state
  const [patients, setPatients] = useState([]);
  const [selectedPatient, setSelectedPatient] = useState(null);
  const [selectedSequence, setSelectedSequence] = useState(null);
  const [frames, setFrames] = useState([]);
  const [currentFrame, setCurrentFrame] = useState(0);
  const [loading, setLoading] = useState(false);
  const [comment, setComment] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const [markedFrame, setMarkedFrame] = useState(null);

  // Check stored token on mount
  useEffect(() => {
    const token = getToken();
    if (!token) {
      setAuthChecking(false);
      return;
    }
    fetch('/api/auth/me', {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => {
        if (res.ok) return res.json();
        throw new Error('Invalid token');
      })
      .then((data) => setUser(data))
      .catch(() => setToken(null))
      .finally(() => setAuthChecking(false));
  }, []);

  // Persist sidebar state
  useEffect(() => {
    saveSidebarState(sidebarWidth, sidebarCollapsed);
  }, [sidebarWidth, sidebarCollapsed]);

  // Sidebar resize handlers
  const handleResizeStart = useCallback((e) => {
    e.preventDefault();
    isResizing.current = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMouseMove = (e) => {
      if (!isResizing.current) return;
      const newWidth = Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, e.clientX));
      setSidebarWidth(newWidth);
    };

    const onMouseUp = () => {
      isResizing.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }, []);

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => !prev);
  }, []);

  // Listen for auth expiry events from api.js
  useEffect(() => {
    const handler = () => {
      setUser(null);
      setToken(null);
    };
    window.addEventListener('auth-expired', handler);
    return () => window.removeEventListener('auth-expired', handler);
  }, []);

  // Login handler
  const handleLogin = useCallback(async (username, password) => {
    const data = await apiLogin(username, password);
    setToken(data.token);
    setUser(data.user);
  }, []);

  // Logout handler
  const handleLogout = useCallback(() => {
    setToken(null);
    setUser(null);
    setSelectedPatient(null);
    setSelectedSequence(null);
    setFrames([]);
  }, []);

  // Load patients
  useEffect(() => {
    if (!user) return;
    fetchPatients().then(setPatients).catch(console.error);
  }, [refreshKey, user]);

  // Load frames when sequence is selected
  useEffect(() => {
    if (!user) return;
    if (!selectedPatient || !selectedSequence) {
      setFrames([]);
      setCurrentFrame(0);
      setMarkedFrame(null);
      return;
    }
    setLoading(true);
    setCurrentFrame(0);
    setComment('');
    setMarkedFrame(null);

    Promise.all([
      fetchFramesBulk(selectedPatient, selectedSequence),
      fetchSequenceAnnotation(selectedPatient, selectedSequence).catch(() => null),
    ])
      .then(([data, annotation]) => {
        const blobUrls = data.frames.map((b64) => {
          const bin = atob(b64);
          const arr = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
          const blob = new Blob([arr], { type: 'image/jpeg' });
          return URL.createObjectURL(blob);
        });
        setFrames(blobUrls);

        if (annotation && annotation.type === 'annotation') {
          setMarkedFrame(annotation.frame_index);
          setCurrentFrame(annotation.frame_index);
          setComment(annotation.comment || '');
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false));

    return () => {
      setFrames((prev) => {
        prev.forEach(URL.revokeObjectURL);
        return [];
      });
    };
  }, [selectedPatient, selectedSequence, user]);

  const selectSequence = useCallback((patientId, seqId) => {
    setSelectedPatient(patientId);
    setSelectedSequence(seqId);
  }, []);

  const advanceToNext = useCallback(() => {
    fetchPatients().then((pts) => {
      setPatients(pts);
      const curPatient = pts.find((p) => p.patient_id === selectedPatient);
      if (curPatient) {
        const nextSeq = curPatient.sequences.find(
          (s) => s.status === 'todo' && s.sequence_id !== selectedSequence
        );
        if (nextSeq) {
          selectSequence(curPatient.patient_id, nextSeq.sequence_id);
          return;
        }
      }
      for (const p of pts) {
        const todoSeq = p.sequences.find((s) => s.status === 'todo');
        if (todoSeq) {
          selectSequence(p.patient_id, todoSeq.sequence_id);
          return;
        }
      }
      setSelectedPatient(null);
      setSelectedSequence(null);
    });
  }, [selectedPatient, selectedSequence, selectSequence]);

  const handleMark = useCallback(async () => {
    if (!selectedPatient || !selectedSequence) return;
    await submitAnnotation({
      patient_id: selectedPatient,
      sequence_id: selectedSequence,
      frame_index: currentFrame,
      comment,
    });
    setMarkedFrame(currentFrame);
    setRefreshKey((k) => k + 1);
  }, [selectedPatient, selectedSequence, currentFrame, comment]);

  const handleSkip = useCallback(async () => {
    if (!selectedPatient || !selectedSequence) return;
    await submitSkip({
      patient_id: selectedPatient,
      sequence_id: selectedSequence,
      reason: comment || 'Zła jakość',
    });
    setMarkedFrame(null);
    setRefreshKey((k) => k + 1);
    advanceToNext();
  }, [selectedPatient, selectedSequence, comment, advanceToNext]);

  const handleExportCoco = useCallback(async () => {
    try {
      const data = await exportCoco();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `keyselector_coco_${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Export failed:', err);
    }
  }, []);

  // Auth checking spinner
  if (authChecking) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-950">
        <div className="text-gray-500 animate-pulse">Sprawdzanie sesji...</div>
      </div>
    );
  }

  // Login page
  if (!user) {
    return <LoginPage onLogin={handleLogin} />;
  }

  return (
    <div className="flex h-screen overflow-hidden">
      {showAdmin && (
        <AdminPanel token={getToken()} onClose={() => setShowAdmin(false)} />
      )}

      {/* Sidebar */}
      <div className="flex shrink-0" style={{ width: sidebarCollapsed ? 40 : sidebarWidth }}>
        <Sidebar
          patients={patients}
          selectedPatient={selectedPatient}
          selectedSequence={selectedSequence}
          onSelect={selectSequence}
          collapsed={sidebarCollapsed}
          onToggleCollapse={toggleSidebar}
        />
        {!sidebarCollapsed && (
          <div
            className="relative w-2 cursor-col-resize shrink-0 group"
            onMouseDown={handleResizeStart}
          >
            {/* Visible thin bar */}
            <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 w-0.5 bg-gray-700 group-hover:bg-blue-500 transition-colors" />
            {/* Wider invisible grab zone */}
            <div className="absolute inset-y-0 -left-1 -right-1" />
          </div>
        )}
      </div>

      {/* Main area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <header className="flex items-center justify-between px-6 py-3 border-b border-gray-800 bg-gray-900/50">
          <div className="flex items-center gap-3">
            <Heart className="w-6 h-6 text-red-500" />
            <h1 className="text-lg font-semibold tracking-tight">
              Keyselector
              <span className="text-gray-500 font-normal ml-2 text-sm">
                Selekcja klatek angiograficznych
              </span>
            </h1>
          </div>
          <div className="flex items-center gap-4">
            <StatsBar refreshKey={refreshKey} />
            <div className="flex items-center gap-2 ml-4 border-l border-gray-800 pl-4">
              <button
                onClick={handleExportCoco}
                className="p-1.5 hover:bg-gray-800 rounded-lg transition-colors"
                title="Eksport COCO JSON"
              >
                <Download className="w-4 h-4 text-green-400" />
              </button>
              {user.role === 'admin' && (
                <button
                  onClick={() => setShowAdmin(true)}
                  className="p-1.5 hover:bg-gray-800 rounded-lg transition-colors"
                  title="Zarządzanie użytkownikami"
                >
                  <Shield className="w-4 h-4 text-blue-400" />
                </button>
              )}
              <span className="text-sm text-gray-400">{user.username}</span>
              <button
                onClick={handleLogout}
                className="p-1.5 hover:bg-gray-800 rounded-lg transition-colors"
                title="Wyloguj"
              >
                <LogOut className="w-4 h-4 text-gray-500 hover:text-red-400" />
              </button>
            </div>
          </div>
        </header>

        {selectedSequence ? (
          <div className="flex-1 flex flex-col lg:flex-row overflow-hidden">
            {/* Viewer */}
            <div className="flex-1 flex flex-col min-w-0">
              <FrameViewer
                frames={frames}
                currentFrame={currentFrame}
                setCurrentFrame={setCurrentFrame}
                loading={loading}
                onMark={handleMark}
                markedFrame={markedFrame}
              />
            </div>

            {/* Action panel */}
            <ActionPanel
              patientId={selectedPatient}
              sequenceId={selectedSequence}
              currentFrame={currentFrame}
              totalFrames={frames.length}
              comment={comment}
              setComment={setComment}
              onMark={handleMark}
              onSkip={handleSkip}
              markedFrame={markedFrame}
              onNext={advanceToNext}
              setCurrentFrame={setCurrentFrame}
            />
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-500">
            <div className="text-center">
              <Heart className="w-16 h-16 mx-auto mb-4 opacity-20" />
              <p className="text-xl">Wybierz projekcję z panelu bocznego</p>
              <p className="text-sm mt-2 text-gray-600">
                lub umieść pliki DICOM/PNG w folderze <code className="text-gray-400">backend/data/</code>
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
