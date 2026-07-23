import { useState } from 'react';
import type { Master, Seat } from '../types';

interface RoundTableProps {
  seats: Seat[];
  onDrop: (seatId: string, master: Master) => void;
  onRemove: (seatId: string) => void;
}

export default function RoundTable({ seats, onDrop, onRemove }: RoundTableProps) {
  const [dragOverSeat, setDragOverSeat] = useState<string | null>(null);

  const handleDragOver = (e: React.DragEvent, seatId: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverSeat(seatId);
  };

  const handleDragLeave = () => setDragOverSeat(null);

  const handleDrop = (e: React.DragEvent, seatId: string) => {
    e.preventDefault();
    setDragOverSeat(null);
    try {
      const master: Master = JSON.parse(e.dataTransfer.getData('application/master'));
      onDrop(seatId, master);
    } catch { /* ignore */ }
  };

  // Seat positions around the table (angle-based for a circle)
  const seatAngles: Record<string, number> = {
    seat_bull: -20,
    seat_fundamentals: 30,
    seat_market: 85,
    seat_bear: 200,
    seat_risk: 250,
    seat_manager: 305,
  };

  const roleIcons: Record<string, string> = {
    bull: '🐂',
    bear: '🐻',
    fundamentals: '📊',
    market: '📈',
    risk_aggressive: '🔥',
    risk_conservative: '🛡️',
    risk_neutral: '⚖️',
    manager: '👑',
  };

  const roleColors: Record<string, string> = {
    bull: '#ef4444',
    bear: '#22c55e',
    fundamentals: '#3b82f6',
    market: '#f59e0b',
    risk_aggressive: '#ef4444',
    risk_conservative: '#22c55e',
    risk_neutral: '#8b5cf6',
    manager: '#ec4899',
  };

  return (
    <div className="relative w-[380px] h-[380px]">
      {/* Center table */}
      <svg viewBox="0 0 380 380" className="w-full h-full">
        {/* Ambient glow */}
        <defs>
          <radialGradient id="tableGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#8B6914" stopOpacity="0.3" />
            <stop offset="60%" stopColor="#8B6914" stopOpacity="0.05" />
            <stop offset="100%" stopColor="#8B6914" stopOpacity="0" />
          </radialGradient>
          <filter id="tableShadow">
            <feDropShadow dx="0" dy="4" stdDeviation="8" floodColor="#000" floodOpacity="0.5" />
          </filter>
        </defs>

        {/* Glow background */}
        <circle cx="190" cy="190" r="170" fill="url(#tableGlow)" />

        {/* Outer ring */}
        <circle cx="190" cy="190" r="110" fill="none" stroke="#8B6914" strokeWidth="1" opacity="0.2" />
        <circle cx="190" cy="190" r="105" fill="none" stroke="#8B6914" strokeWidth="0.5" opacity="0.1" strokeDasharray="4 4" />

        {/* Table surface */}
        <circle cx="190" cy="190" r="95" fill="#2a1f0a" opacity="0.8" filter="url(#tableShadow)" />
        <circle cx="190" cy="190" r="95" fill="url(#tableGlow)" />

        {/* Wood grain effect */}
        <circle cx="190" cy="190" r="85" fill="none" stroke="#8B6914" strokeWidth="0.5" opacity="0.15" />
        <circle cx="190" cy="190" r="70" fill="none" stroke="#8B6914" strokeWidth="0.5" opacity="0.1" />
        <circle cx="190" cy="190" r="50" fill="none" stroke="#8B6914" strokeWidth="0.5" opacity="0.08" />
        <circle cx="190" cy="190" r="25" fill="none" stroke="#8B6914" strokeWidth="0.5" opacity="0.05" />

        {/* Center text */}
        <text x="190" y="185" textAnchor="middle" fill="#8B6914" fontSize="11" opacity="0.5" fontFamily="serif">
          圆桌会议
        </text>
        <text x="190" y="200" textAnchor="middle" fill="#8B6914" fontSize="9" opacity="0.3" fontFamily="serif">
          ROUND TABLE
        </text>
      </svg>

      {/* Seat slots positioned around the table */}
      {seats.map(seat => {
        const angle = seatAngles[seat.id] ?? 0;
        const rad = (angle - 90) * (Math.PI / 180);
        const cx = 190 + 160 * Math.cos(rad);
        const cy = 190 + 160 * Math.sin(rad);

        return (
          <div
            key={seat.id}
            className="absolute"
            style={{
              left: `${cx - 40}px`,
              top: `${cy - 40}px`,
              width: '80px',
              height: '80px',
            }}
            onDragOver={e => handleDragOver(e, seat.id)}
            onDragLeave={handleDragLeave}
            onDrop={e => handleDrop(e, seat.id)}
          >
            {seat.master ? (
              /* Filled seat */
              <div className={`relative w-full h-full group ${dragOverSeat === seat.id ? 'scale-110' : ''} transition-transform`}>
                <div
                  className="w-full h-full rounded-full bg-gradient-to-br from-amber-600/30 to-amber-800/30 
                           border-2 border-amber-500/40 flex flex-col items-center justify-center cursor-pointer
                           hover:border-amber-400/60 transition-all duration-200"
                  onClick={() => onRemove(seat.id)}
                  title={`点击移除 ${seat.master.name}`}
                >
                  <span className="text-2xl">{seat.master.avatar_url}</span>
                  <span className="text-[10px] text-amber-300 max-w-[64px] truncate text-center mt-0.5">
                    {seat.master.name}
                  </span>
                  <span className="text-[9px] text-white/30 -mt-0.5">{seat.label}</span>
                </div>
                {/* Remove hint */}
                <div className="absolute inset-0 rounded-full bg-red-500/60 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
                  <span className="text-white text-xs">✕ 移除</span>
                </div>
              </div>
            ) : (
              /* Empty seat */
              <div
                className={`w-full h-full rounded-full border-2 border-dashed flex flex-col items-center justify-center 
                         transition-all duration-200 cursor-pointer
                         ${dragOverSeat === seat.id
                    ? 'border-amber-400/80 bg-amber-500/20 scale-110 shadow-lg shadow-amber-500/30'
                    : 'border-white/15 bg-white/3 hover:border-white/30 hover:bg-white/5'
                  }`}
              >
                <span className="text-lg opacity-60">{roleIcons[seat.role] || '💺'}</span>
                <span className="text-[10px] text-white/50 mt-1 text-center leading-tight">{seat.label}</span>
                <span className="text-[8px] text-white/20 mt-0.5">拖入大师</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
