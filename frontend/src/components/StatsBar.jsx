import React, { useState, useEffect } from 'react';
import { fetchStats } from '../api';
import { BarChart3 } from 'lucide-react';

export default function StatsBar({ refreshKey, datasetId }) {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    fetchStats(datasetId).then(setStats).catch(() => {});
  }, [refreshKey, datasetId]);

  if (!stats) return null;

  const pct =
    stats.total_sequences > 0
      ? Math.round(((stats.done + stats.skipped) / stats.total_sequences) * 100)
      : 0;

  return (
    <div className="flex items-center gap-4 text-xs text-gray-400">
      <BarChart3 className="w-4 h-4" />
      <span>
        <span className="text-green-400 font-medium">{stats.done}</span> oznaczonych
      </span>
      <span>
        <span className="text-yellow-400 font-medium">{stats.skipped}</span> pominiętych
      </span>
      <span>
        <span className="text-gray-300 font-medium">{stats.remaining}</span> pozostało
      </span>
      <div className="w-24 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div
          className="h-full bg-green-500 rounded-full transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="font-mono">{pct}%</span>
    </div>
  );
}
