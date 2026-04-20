import React from 'react';
import { CheckCircle, XCircle, MessageSquare, Star, ArrowRight } from 'lucide-react';

export default function ActionPanel({
  patientId,
  sequenceId,
  currentFrame,
  totalFrames,
  comment,
  setComment,
  onMark,
  onSkip,
  markedFrame,
  onNext,
  setCurrentFrame,
}) {
  return (
    <aside className="w-80 shrink-0 border-l border-gray-800 bg-gray-900/50 flex flex-col p-4 gap-4">
      {/* Current selection info */}
      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
          Aktywna projekcja
        </h3>
        <div className="bg-gray-800 rounded-lg p-3 space-y-1">
          <div className="flex items-center justify-between text-sm">
            <span className="text-gray-400">Pacjent</span>
            <span className="text-white font-medium">{patientId}</span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-gray-400">Sekwencja</span>
            <span className="text-white font-medium">{sequenceId}</span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-gray-400">Klatka</span>
            <span className="text-blue-400 font-mono font-medium">
              {currentFrame + 1} / {totalFrames}
            </span>
          </div>
        </div>
      </div>

      {/* Marked frame indicator */}
      {markedFrame !== null && (
        <div className="bg-green-900/30 border border-green-700/50 rounded-lg p-3 space-y-2">
          <div className="flex items-center gap-2 text-green-400 text-sm font-semibold">
            <Star className="w-4 h-4 fill-current" />
            Oznaczona klatka informatywna
          </div>
          <div className="flex items-center justify-between">
            <span className="text-green-300/70 text-sm">Klatka nr</span>
            <span className="text-green-300 font-mono font-bold text-lg">{markedFrame + 1}</span>
          </div>
          <button
            onClick={() => setCurrentFrame(markedFrame)}
            className="w-full text-xs text-green-400 hover:text-green-300 underline underline-offset-2 transition-colors"
          >
            Przejdź do oznaczonej klatki
          </button>
        </div>
      )}

      {/* Comment */}
      <div className="space-y-2">
        <label className="flex items-center gap-2 text-sm text-gray-400">
          <MessageSquare className="w-4 h-4" />
          Komentarz (opcjonalny)
        </label>
        <textarea
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Np. dobra widoczność LAD, lekki ruch..."
          rows={3}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 resize-none"
        />
      </div>

      {/* Action buttons */}
      <div className="space-y-2 mt-auto">
        <button
          onClick={onMark}
          className={`w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg font-semibold transition-colors text-sm ${
            markedFrame !== null
              ? 'bg-green-700 hover:bg-green-600 text-white'
              : 'bg-green-600 hover:bg-green-500 text-white'
          }`}
        >
          <CheckCircle className="w-5 h-5" />
          {markedFrame !== null
            ? `Zmień na klatkę ${currentFrame + 1}`
            : 'Oznacz jako klatkę informatywną'}
        </button>

        {markedFrame !== null && (
          <button
            onClick={onNext}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium transition-colors text-sm"
          >
            <ArrowRight className="w-4 h-4" />
            Następna projekcja
          </button>
        )}

        <button
          onClick={onSkip}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-gray-800 hover:bg-gray-700 border border-gray-700 text-yellow-400 font-medium transition-colors text-sm"
        >
          <XCircle className="w-4 h-4" />
          Pomiń / Zła jakość
        </button>
      </div>

      {/* Keyboard shortcut reminder */}
      <div className="border-t border-gray-800 pt-3">
        <p className="text-xs text-gray-600 leading-relaxed">
          <strong className="text-gray-500">Skróty:</strong> Enter = Oznacz,
          Spacja = Play/Pause, ← → = Klatki
        </p>
      </div>
    </aside>
  );
}
