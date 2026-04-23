import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  Play, Pause, SkipBack, SkipForward, Gauge, Star,
  Repeat, ZoomIn, ZoomOut, Maximize, SlidersHorizontal, RotateCcw,
  Scissors, X, Info,
} from 'lucide-react';
import { getUserColor } from '../userColors';
import { fetchSequenceMetadata } from '../api';

const SPEED_OPTIONS = [0.5, 1, 2, 5, 10, 15, 30];
const ZOOM_STEP = 0.25;
const MIN_ZOOM = 1;
const MAX_ZOOM = 8;

export default function FrameViewer({ frames, currentFrame, setCurrentFrame, loading, onMark, markedFrame, allAnnotations = [], currentUsername, datasetId, patientId, sequenceId, hasMetadata = false }) {
  const [playing, setPlaying] = useState(false);
  const [fps, setFps] = useState(10);
  const [loop, setLoop] = useState(false);
  const intervalRef = useRef(null);
  const containerRef = useRef(null);
  const imgContainerRef = useRef(null);

  // Zoom & pan state
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const isPanning = useRef(false);
  const panStart = useRef({ x: 0, y: 0 });
  const panOrigin = useRef({ x: 0, y: 0 });

  // Image adjustments
  const [showAdjust, setShowAdjust] = useState(false);
  const [brightness, setBrightness] = useState(100);
  const [contrast, setContrast] = useState(100);
  const [gamma, setGamma] = useState(1);
  const [invert, setInvert] = useState(false);

  // In/Out points
  const [inPoint, setInPoint] = useState(null);
  const [outPoint, setOutPoint] = useState(null);
  const hasRange = inPoint !== null && outPoint !== null;

  // Sequence metadata (DICOM tags / sidecar)
  const [showInfo, setShowInfo] = useState(false);
  const [metaFields, setMetaFields] = useState([]);
  const [metaLoading, setMetaLoading] = useState(false);
  const [metaError, setMetaError] = useState('');

  const frameCount = frames.length;

  // CSS filter string
  const filterStyle = useMemo(() => {
    const parts = [];
    if (brightness !== 100) parts.push(`brightness(${brightness}%)`);
    if (contrast !== 100) parts.push(`contrast(${contrast}%)`);
    if (invert) parts.push('invert(1)');
    // Gamma via SVG filter is complex — approximate with a combination
    // gamma < 1 = brighter midtones, gamma > 1 = darker midtones
    // We approximate gamma using CSS: brightness * contrast shift
    return parts.join(' ') || 'none';
  }, [brightness, contrast, invert]);

  // SVG filter for gamma (CSS has no native gamma)
  const gammaFilterId = 'gamma-filter';
  const needsGamma = gamma !== 1;

  const resetAdjustments = () => {
    setBrightness(100);
    setContrast(100);
    setGamma(1);
    setInvert(false);
  };

  const resetZoom = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  // Clear in/out when sequence changes
  useEffect(() => {
    setInPoint(null);
    setOutPoint(null);
  }, [frames]);

  // Reset / fetch metadata when sequence changes
  useEffect(() => {
    setMetaFields([]);
    setMetaError('');
    if (!showInfo) return;
    if (datasetId == null || !patientId || !sequenceId) return;
    let cancelled = false;
    setMetaLoading(true);
    fetchSequenceMetadata(datasetId, patientId, sequenceId)
      .then((data) => {
        if (cancelled) return;
        setMetaFields(Array.isArray(data.fields) ? data.fields : []);
      })
      .catch((err) => {
        if (cancelled) return;
        setMetaError(err.message || 'Błąd pobierania metadanych');
      })
      .finally(() => { if (!cancelled) setMetaLoading(false); });
    return () => { cancelled = true; };
  }, [showInfo, datasetId, patientId, sequenceId]);

  // Playback with loop + in/out range support
  useEffect(() => {
    if (playing && frameCount > 0) {
      const rangeStart = hasRange ? inPoint : 0;
      const rangeEnd = hasRange ? outPoint : frameCount - 1;
      intervalRef.current = setInterval(() => {
        setCurrentFrame((prev) => {
          if (prev >= rangeEnd) {
            if (loop) return rangeStart;
            setPlaying(false);
            return prev;
          }
          return prev + 1;
        });
      }, 1000 / fps);
    }
    return () => clearInterval(intervalRef.current);
  }, [playing, fps, frameCount, setCurrentFrame, loop, hasRange, inPoint, outPoint]);

  // Stop playback & reset zoom when frames change
  useEffect(() => {
    setPlaying(false);
    resetZoom();
  }, [frames]);

  // --- Zoom: mouse wheel ---
  const handleWheel = useCallback(
    (e) => {
      if (frameCount === 0) return;
      e.preventDefault();
      const delta = e.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP;
      setZoom((z) => {
        const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z + delta));
        if (next === MIN_ZOOM) setPan({ x: 0, y: 0 });
        return next;
      });
    },
    [frameCount]
  );

  useEffect(() => {
    const el = imgContainerRef.current;
    if (!el) return;
    el.addEventListener('wheel', handleWheel, { passive: false });
    return () => el.removeEventListener('wheel', handleWheel);
  }, [handleWheel]);

  // --- Pan: mouse drag when zoomed ---
  const handleMouseDown = useCallback(
    (e) => {
      if (zoom <= 1) return;
      isPanning.current = true;
      panStart.current = { x: e.clientX, y: e.clientY };
      panOrigin.current = { ...pan };
      e.preventDefault();
    },
    [zoom, pan]
  );

  const handleMouseMove = useCallback(
    (e) => {
      if (!isPanning.current) return;
      setPan({
        x: panOrigin.current.x + (e.clientX - panStart.current.x),
        y: panOrigin.current.y + (e.clientY - panStart.current.y),
      });
    },
    []
  );

  const handleMouseUp = useCallback(() => {
    isPanning.current = false;
  }, []);

  // Keyboard shortcuts
  const handleKeyDown = useCallback(
    (e) => {
      if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;

      switch (e.key) {
        case 'ArrowLeft':
          e.preventDefault();
          setCurrentFrame((prev) => Math.max(0, prev - 1));
          break;
        case 'ArrowRight':
          e.preventDefault();
          setCurrentFrame((prev) => Math.min(frameCount - 1, prev + 1));
          break;
        case ' ':
          e.preventDefault();
          setPlaying((p) => !p);
          break;
        case 'Enter':
          e.preventDefault();
          onMark();
          break;
        case 'i':
          setInPoint(currentFrame);
          break;
        case 'o':
          setOutPoint(currentFrame);
          break;
        case 'x':
          setInPoint(null);
          setOutPoint(null);
          break;
        case 'l':
          setLoop((l) => !l);
          break;
        case 'r':
          resetZoom();
          break;
        case '+':
        case '=':
          setZoom((z) => Math.min(MAX_ZOOM, z + ZOOM_STEP));
          break;
        case '-':
          setZoom((z) => {
            const next = Math.max(MIN_ZOOM, z - ZOOM_STEP);
            if (next === MIN_ZOOM) setPan({ x: 0, y: 0 });
            return next;
          });
          break;
        default:
          break;
      }
    },
    [frameCount, setCurrentFrame, onMark]
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  const togglePlay = () => setPlaying((p) => !p);

  return (
    <div ref={containerRef} className="flex-1 flex flex-col p-4 gap-3 min-h-0">
      {/* SVG gamma filter definition */}
      {needsGamma && (
        <svg width="0" height="0" className="absolute">
          <defs>
            <filter id={gammaFilterId}>
              <feComponentTransfer>
                <feFuncR type="gamma" amplitude="1" exponent={gamma} offset="0" />
                <feFuncG type="gamma" amplitude="1" exponent={gamma} offset="0" />
                <feFuncB type="gamma" amplitude="1" exponent={gamma} offset="0" />
              </feComponentTransfer>
            </filter>
          </defs>
        </svg>
      )}

      {/* Image display */}
      <div
        ref={imgContainerRef}
        className={`flex-1 flex items-center justify-center bg-black rounded-xl overflow-hidden min-h-0 relative ring-2 ${
          allAnnotations.some((a) => a.frame_index === currentFrame)
            ? (() => {
                const match = allAnnotations.find((a) => a.frame_index === currentFrame);
                return match ? getUserColor(match.user_id).ring : 'ring-transparent';
              })()
            : 'ring-transparent'
        }`}
        style={{ cursor: zoom > 1 ? (isPanning.current ? 'grabbing' : 'grab') : 'default' }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        {loading ? (
          <div className="text-gray-500 animate-pulse">Ładowanie klatek...</div>
        ) : frameCount === 0 ? (
          <div className="text-gray-600">Brak klatek</div>
        ) : (
          <img
            src={frames[currentFrame]}
            alt={`Klatka ${currentFrame + 1}`}
            className="max-w-full max-h-full object-contain select-none"
            draggable={false}
            style={{
              transform: `scale(${zoom}) translate(${pan.x / zoom}px, ${pan.y / zoom}px)`,
              filter: [
                filterStyle !== 'none' ? filterStyle : '',
                needsGamma ? `url(#${gammaFilterId})` : '',
              ].filter(Boolean).join(' ') || 'none',
              transition: isPanning.current ? 'none' : 'transform 0.15s ease-out',
            }}
          />
        )}

        {/* Frame counter overlay */}
        {frameCount > 0 && (
          <div className="absolute top-3 right-3 bg-black/70 text-white text-sm px-3 py-1 rounded-lg font-mono">
            {currentFrame + 1} / {frameCount}
          </div>
        )}

        {/* Metadata panel */}
        {showInfo && hasMetadata && (
          <div className="absolute top-14 right-3 max-w-xs bg-black/80 border border-gray-700 rounded-lg text-xs text-gray-200 shadow-lg overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700/70 bg-gray-900/60">
              <div className="flex items-center gap-2">
                <Info className="w-3.5 h-3.5 text-emerald-400" />
                <span className="font-semibold">Metadane</span>
              </div>
              <button
                onClick={() => setShowInfo(false)}
                className="text-gray-500 hover:text-white"
                title="Ukryj"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="px-3 py-2 space-y-1 max-h-64 overflow-y-auto">
              {metaLoading && <div className="text-gray-500">Ładowanie…</div>}
              {metaError && <div className="text-red-400">{metaError}</div>}
              {!metaLoading && !metaError && metaFields.length === 0 && (
                <div className="text-gray-500">Brak dostępnych pól.</div>
              )}
              {metaFields.map((f) => (
                <div key={f.tag} className="flex items-baseline gap-2">
                  <span className="text-gray-400 min-w-[110px] shrink-0">{f.label}</span>
                  <span className="font-mono text-gray-100">{f.display}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Zoom level indicator */}
        {zoom > 1 && (
          <div className="absolute bottom-3 right-3 bg-black/70 text-blue-300 text-xs px-2 py-1 rounded-lg font-mono">
            {zoom.toFixed(1)}×
          </div>
        )}

        {/* Loop badge */}
        {loop && (
          <div className="absolute bottom-3 left-3 bg-blue-600/80 text-white text-xs px-2 py-1 rounded-lg flex items-center gap-1">
            <Repeat className="w-3 h-3" />
            Pętla
          </div>
        )}

        {/* In/Out range badge */}
        {hasRange && (
          <div className="absolute bottom-3 left-1/2 -translate-x-1/2 bg-cyan-700/80 text-white text-xs px-3 py-1 rounded-lg flex items-center gap-2 font-mono">
            <Scissors className="w-3 h-3" />
            <span>IN {inPoint + 1}</span>
            <span className="text-cyan-300">—</span>
            <span>OUT {outPoint + 1}</span>
            <span className="text-cyan-300/70">({outPoint - inPoint + 1} kl.)</span>
          </div>
        )}

        {/* Marked frame badges (all users on current frame) */}
        {allAnnotations
          .filter((a) => a.frame_index === currentFrame)
          .map((ann) => {
            const color = getUserColor(ann.user_id);
            const isMe = ann.user_id === currentUsername;
            return (
              <div
                key={ann.user_id}
                className="absolute top-3 left-3 text-white text-xs px-3 py-1.5 rounded-lg flex items-center gap-1.5 font-semibold shadow-lg"
                style={{
                  backgroundColor: color.hex + 'e6',
                  top: `${12 + allAnnotations.filter((a) => a.frame_index === currentFrame).indexOf(ann) * 32}px`,
                }}
              >
                <Star className="w-3.5 h-3.5 fill-current" />
                {isMe ? 'Klatka informatywna' : ann.user_id}
              </div>
            );
          })
        }
      </div>

      {/* Image adjustments panel */}
      {showAdjust && (
        <div className="bg-gray-800/90 rounded-lg p-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-gray-300">
          {/* Brightness */}
          <label className="flex items-center gap-2 min-w-[160px]">
            <span className="w-20">Jasność</span>
            <input type="range" min={0} max={300} value={brightness}
              onChange={(e) => setBrightness(Number(e.target.value))}
              className="flex-1 h-1.5 accent-yellow-400 bg-gray-700 rounded cursor-pointer" />
            <span className="w-10 text-right font-mono">{brightness}%</span>
          </label>
          {/* Contrast */}
          <label className="flex items-center gap-2 min-w-[160px]">
            <span className="w-20">Kontrast</span>
            <input type="range" min={0} max={300} value={contrast}
              onChange={(e) => setContrast(Number(e.target.value))}
              className="flex-1 h-1.5 accent-orange-400 bg-gray-700 rounded cursor-pointer" />
            <span className="w-10 text-right font-mono">{contrast}%</span>
          </label>
          {/* Gamma */}
          <label className="flex items-center gap-2 min-w-[160px]">
            <span className="w-20">Gamma</span>
            <input type="range" min={0.1} max={3} step={0.05} value={gamma}
              onChange={(e) => setGamma(Number(e.target.value))}
              className="flex-1 h-1.5 accent-purple-400 bg-gray-700 rounded cursor-pointer" />
            <span className="w-10 text-right font-mono">{gamma.toFixed(2)}</span>
          </label>
          {/* Invert */}
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input type="checkbox" checked={invert}
              onChange={(e) => setInvert(e.target.checked)}
              className="accent-red-400 w-3.5 h-3.5" />
            <span>Negatyw</span>
          </label>
          {/* Reset */}
          <button
            onClick={resetAdjustments}
            className="flex items-center gap-1 text-gray-400 hover:text-white transition-colors ml-auto"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Reset
          </button>
        </div>
      )}

      {/* Controls bar */}
      {frameCount > 0 && (
        <div className="flex flex-col gap-2">
          {/* Timeline with in/out range */}
          <div className="relative h-6 flex items-center">
            {/* In/Out range highlight bar */}
            {hasRange && frameCount > 1 && (
              <div
                className="absolute h-2 bg-cyan-500/30 rounded-sm pointer-events-none z-0"
                style={{
                  left: `${(inPoint / (frameCount - 1)) * 100}%`,
                  width: `${((outPoint - inPoint) / (frameCount - 1)) * 100}%`,
                }}
              />
            )}

            {/* In point marker */}
            {inPoint !== null && frameCount > 1 && (
              <button
                onClick={() => setCurrentFrame(inPoint)}
                className="absolute top-1/2 -translate-y-1/2 z-20 flex flex-col items-center group"
                style={{ left: `${(inPoint / (frameCount - 1)) * 100}%` }}
                title={`IN: klatka ${inPoint + 1}`}
              >
                <div className="w-0.5 h-5 bg-cyan-400 rounded-full group-hover:bg-cyan-300" />
                <span className="absolute -top-4 text-[9px] font-mono text-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">IN {inPoint + 1}</span>
              </button>
            )}

            {/* Out point marker */}
            {outPoint !== null && frameCount > 1 && (
              <button
                onClick={() => setCurrentFrame(outPoint)}
                className="absolute top-1/2 -translate-y-1/2 z-20 flex flex-col items-center group"
                style={{ left: `${(outPoint / (frameCount - 1)) * 100}%` }}
                title={`OUT: klatka ${outPoint + 1}`}
              >
                <div className="w-0.5 h-5 bg-cyan-400 rounded-full group-hover:bg-cyan-300" />
                <span className="absolute -top-4 text-[9px] font-mono text-cyan-400 opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">OUT {outPoint + 1}</span>
              </button>
            )}

            {/* Slider */}
            <input
              type="range"
              min={0}
              max={frameCount - 1}
              value={currentFrame}
              onChange={(e) => setCurrentFrame(Number(e.target.value))}
              className="w-full h-2 rounded-lg appearance-none cursor-pointer bg-gray-700 accent-blue-500 relative z-10"
            />

            {/* Marker diamonds on slider for all annotated frames */}
            {allAnnotations.map((ann) => {
              if (frameCount <= 1) return null;
              const color = getUserColor(ann.user_id);
              const isMe = ann.user_id === currentUsername;
              return (
                <button
                  key={ann.user_id}
                  onClick={() => setCurrentFrame(ann.frame_index)}
                  className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-3.5 h-3.5 rounded-sm rotate-45 border-2 cursor-pointer hover:scale-125 transition-transform z-20"
                  style={{
                    left: `${(ann.frame_index / (frameCount - 1)) * 100}%`,
                    backgroundColor: color.hex,
                    borderColor: color.hex + '99',
                    boxShadow: `0 0 8px ${color.hex}80`,
                    opacity: isMe ? 1 : 0.7,
                  }}
                  title={`${ann.user_id}: klatka ${ann.frame_index + 1}`}
                />
              );
            })}
          </div>

          {/* Playback controls */}
          <div className="flex flex-wrap gap-2 items-center">
            {/* Group: Playback */}
            <div className="flex items-center gap-1">
              <button
                onClick={() => setCurrentFrame(0)}
                className="p-2 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-white transition-colors"
                title="Pierwsza klatka"
              >
                <SkipBack className="w-4 h-4" />
              </button>

              <button
                onClick={togglePlay}
                className="p-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white transition-colors"
                title={playing ? 'Pauza (Spacja)' : 'Odtwórz (Spacja)'}
              >
                {playing ? <Pause className="w-5 h-5" /> : <Play className="w-5 h-5" />}
              </button>

              <button
                onClick={() => setCurrentFrame(frameCount - 1)}
                className="p-2 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-white transition-colors"
                title="Ostatnia klatka"
              >
                <SkipForward className="w-4 h-4" />
              </button>

              {/* Loop toggle */}
              <button
                onClick={() => setLoop((l) => !l)}
                className={`p-2 rounded-lg transition-colors ${
                  loop
                    ? 'bg-blue-600/30 text-blue-400 hover:bg-blue-600/50'
                    : 'hover:bg-gray-800 text-gray-500 hover:text-gray-300'
                }`}
                title={`Pętla: ${loop ? 'WŁ' : 'WYŁ'} (L)`}
              >
                <Repeat className="w-4 h-4" />
              </button>
            </div>

            <div className="w-px h-5 bg-gray-700" />

            {/* Group: IN/OUT range */}
            <div className="flex items-center gap-1">
              <button
                onClick={() => setInPoint(currentFrame)}
                className={`px-1.5 py-1 rounded text-xs font-mono transition-colors ${
                  inPoint !== null
                    ? 'bg-cyan-600/30 text-cyan-400'
                    : 'hover:bg-gray-800 text-gray-500 hover:text-gray-300'
                }`}
                title={`Ustaw IN (I)${inPoint !== null ? ` — klatka ${inPoint + 1}` : ''}`}
              >
                IN
              </button>
              <button
                onClick={() => setOutPoint(currentFrame)}
                className={`px-1.5 py-1 rounded text-xs font-mono transition-colors ${
                  outPoint !== null
                    ? 'bg-cyan-600/30 text-cyan-400'
                    : 'hover:bg-gray-800 text-gray-500 hover:text-gray-300'
                }`}
                title={`Ustaw OUT (O)${outPoint !== null ? ` — klatka ${outPoint + 1}` : ''}`}
              >
                OUT
              </button>
              {(inPoint !== null || outPoint !== null) && (
                <button
                  onClick={() => { setInPoint(null); setOutPoint(null); }}
                  className="p-1 rounded hover:bg-gray-800 text-gray-500 hover:text-red-400 transition-colors"
                  title="Wyczyść IN/OUT (X)"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>

            <div className="w-px h-5 bg-gray-700" />

            {/* Group: Zoom + Adjustments */}
            <div className="flex items-center gap-1">
              <button
                onClick={() => setZoom((z) => Math.max(MIN_ZOOM, z - ZOOM_STEP))}
                className="p-2 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-white transition-colors"
                title="Oddal (−)"
              >
                <ZoomOut className="w-4 h-4" />
              </button>
              <button
                onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z + ZOOM_STEP))}
                className="p-2 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-white transition-colors"
                title="Przybliż (+)"
              >
                <ZoomIn className="w-4 h-4" />
              </button>
              <button
                onClick={resetZoom}
                className="p-2 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-white transition-colors"
                title="Reset widoku (R)"
              >
                <Maximize className="w-4 h-4" />
              </button>
              <button
                onClick={() => setShowAdjust((s) => !s)}
                className={`p-2 rounded-lg transition-colors ${
                  showAdjust
                    ? 'bg-purple-600/30 text-purple-400 hover:bg-purple-600/50'
                    : 'hover:bg-gray-800 text-gray-500 hover:text-gray-300'
                }`}
                title="Korekcja obrazu"
              >
                <SlidersHorizontal className="w-4 h-4" />
              </button>
              {hasMetadata && (
                <button
                  onClick={() => setShowInfo((s) => !s)}
                  className={`p-2 rounded-lg transition-colors ${
                    showInfo
                      ? 'bg-emerald-600/30 text-emerald-400 hover:bg-emerald-600/50'
                      : 'hover:bg-gray-800 text-gray-500 hover:text-gray-300'
                  }`}
                  title="Metadane sekwencji"
                >
                  <Info className="w-4 h-4" />
                </button>
              )}
            </div>

            <div className="w-px h-5 bg-gray-700" />

            {/* Group: Speed control */}
            <div className="flex items-center gap-2">
              <Gauge className="w-4 h-4 text-gray-500 shrink-0" />
              <div className="flex flex-wrap gap-1">
                {SPEED_OPTIONS.map((spd) => (
                  <button
                    key={spd}
                    onClick={() => setFps(spd)}
                    className={`px-2 py-0.5 text-xs rounded transition-colors ${
                      fps === spd
                        ? 'bg-blue-600 text-white'
                        : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                    }`}
                  >
                    {spd}fps
                  </button>
                ))}
              </div>
            </div>

            {/* Group: Keyboard hints */}
            <div className="hidden lg:flex items-center gap-3 text-xs text-gray-600 ml-auto">
              <span>← → klatki</span>
              <span>Spacja play</span>
              <span>I/O in/out</span>
              <span>L pętla</span>
              <span>+/− zoom</span>
              <span>R reset</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
