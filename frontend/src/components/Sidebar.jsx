import React, { useState } from 'react';
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  User,
  Film,
  CheckCircle2,
  XCircle,
  Circle,
} from 'lucide-react';

const STATUS_ICON = {
  done: <CheckCircle2 className="w-4 h-4 text-green-500 shrink-0" />,
  skipped: <XCircle className="w-4 h-4 text-yellow-500 shrink-0" />,
  todo: <Circle className="w-4 h-4 text-gray-600 shrink-0" />,
};

export default function Sidebar({ patients, selectedPatient, selectedSequence, onSelect, collapsed, onToggleCollapse }) {
  const [expanded, setExpanded] = useState({});

  const toggle = (id) =>
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));

  if (collapsed) {
    return (
      <aside className="w-full shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col h-full" style={{ width: 40 }}>
        <div className="flex items-center justify-center py-3 border-b border-gray-800">
          <button
            onClick={onToggleCollapse}
            className="p-1 hover:bg-gray-800 rounded transition-colors"
            title="Rozwiń panel"
          >
            <ChevronRight className="w-4 h-4 text-gray-400" />
          </button>
        </div>
      </aside>
    );
  }

  return (
    <aside className="w-full shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col h-full">
      <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
          Pacjenci
        </h2>
        <button
          onClick={onToggleCollapse}
          className="p-1 hover:bg-gray-800 rounded transition-colors"
          title="Zwiń panel"
        >
          <ChevronLeft className="w-4 h-4 text-gray-400" />
        </button>
      </div>
      <nav className="flex-1 overflow-y-auto py-2">
        {patients.length === 0 && (
          <p className="px-4 py-8 text-sm text-gray-600 text-center">
            Brak danych.<br />
            Umieść pliki w <code>backend/data/</code>
          </p>
        )}
        {patients.map((patient) => {
          const isExpanded = expanded[patient.patient_id] ?? (patient.patient_id === selectedPatient);
          const doneCount = patient.sequences.filter((s) => s.status !== 'todo').length;
          const totalCount = patient.sequences.length;

          return (
            <div key={patient.patient_id}>
              <button
                onClick={() => toggle(patient.patient_id)}
                className={`w-full flex items-center gap-2 px-4 py-2 text-sm hover:bg-gray-800 transition-colors ${
                  patient.patient_id === selectedPatient
                    ? 'bg-gray-800/50 text-white'
                    : 'text-gray-300'
                }`}
              >
                {isExpanded ? (
                  <ChevronDown className="w-4 h-4 shrink-0 text-gray-500" />
                ) : (
                  <ChevronRight className="w-4 h-4 shrink-0 text-gray-500" />
                )}
                <User className="w-4 h-4 shrink-0 text-blue-400" />
                <span className="truncate flex-1 text-left">{patient.patient_id}</span>
                <span className="text-xs text-gray-500">
                  {doneCount}/{totalCount}
                </span>
              </button>

              {isExpanded && (
                <div className="ml-6 border-l border-gray-800">
                  {patient.sequences.map((seq) => {
                    const isActive =
                      patient.patient_id === selectedPatient &&
                      seq.sequence_id === selectedSequence;

                    return (
                      <button
                        key={seq.sequence_id}
                        onClick={() => onSelect(patient.patient_id, seq.sequence_id)}
                        className={`w-full flex items-center gap-2 pl-4 pr-4 py-1.5 text-sm transition-colors ${
                          isActive
                            ? 'bg-blue-600/20 text-blue-300 border-l-2 border-blue-500 -ml-px'
                            : 'hover:bg-gray-800 text-gray-400'
                        }`}
                      >
                        <Film className="w-3.5 h-3.5 shrink-0" />
                        <span className="truncate flex-1 text-left">{seq.sequence_id}</span>
                        {STATUS_ICON[seq.status]}
                        <span className="text-xs text-gray-600">{seq.frame_count}f</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
