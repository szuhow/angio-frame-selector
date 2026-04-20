const USER_COLORS = [
  { hex: '#22c55e', name: 'green',  ring: 'ring-green-500',  bg: 'bg-green-900/30', border: 'border-green-700/50', text: 'text-green-400', textLight: 'text-green-300', textMuted: 'text-green-300/70' },
  { hex: '#3b82f6', name: 'blue',   ring: 'ring-blue-500',   bg: 'bg-blue-900/30',  border: 'border-blue-700/50',  text: 'text-blue-400',  textLight: 'text-blue-300',  textMuted: 'text-blue-300/70' },
  { hex: '#a855f7', name: 'purple', ring: 'ring-purple-500', bg: 'bg-purple-900/30', border: 'border-purple-700/50', text: 'text-purple-400', textLight: 'text-purple-300', textMuted: 'text-purple-300/70' },
  { hex: '#f97316', name: 'orange', ring: 'ring-orange-500', bg: 'bg-orange-900/30', border: 'border-orange-700/50', text: 'text-orange-400', textLight: 'text-orange-300', textMuted: 'text-orange-300/70' },
  { hex: '#ec4899', name: 'pink',   ring: 'ring-pink-500',   bg: 'bg-pink-900/30',  border: 'border-pink-700/50',  text: 'text-pink-400',  textLight: 'text-pink-300',  textMuted: 'text-pink-300/70' },
  { hex: '#06b6d4', name: 'cyan',   ring: 'ring-cyan-500',   bg: 'bg-cyan-900/30',  border: 'border-cyan-700/50',  text: 'text-cyan-400',  textLight: 'text-cyan-300',  textMuted: 'text-cyan-300/70' },
  { hex: '#eab308', name: 'yellow', ring: 'ring-yellow-500', bg: 'bg-yellow-900/30', border: 'border-yellow-700/50', text: 'text-yellow-400', textLight: 'text-yellow-300', textMuted: 'text-yellow-300/70' },
  { hex: '#ef4444', name: 'red',    ring: 'ring-red-500',    bg: 'bg-red-900/30',   border: 'border-red-700/50',   text: 'text-red-400',   textLight: 'text-red-300',   textMuted: 'text-red-300/70' },
];

function hashCode(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash) + str.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

export function getUserColor(username) {
  return USER_COLORS[hashCode(username) % USER_COLORS.length];
}
