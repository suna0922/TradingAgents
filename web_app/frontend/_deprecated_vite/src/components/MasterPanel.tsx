import { useState } from 'react';
import type { Master } from '../types';

interface MasterPanelProps {
  masters: Master[];
}

const styleLabels: Record<string, string> = {
  value: '价值投资',
  growth: '成长投资',
  momentum: '趋势投资',
  quant: '量化投资',
  macro: '宏观对冲',
  contrarian: '逆向投资',
};

const styleColors: Record<string, string> = {
  value: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
  growth: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
  momentum: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
  quant: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  macro: 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30',
  contrarian: 'bg-rose-500/20 text-rose-300 border-rose-500/30',
};

export default function MasterPanel({ masters }: MasterPanelProps) {
  const [expandedMaster, setExpandedMaster] = useState<string | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);

  const handleDragStart = (e: React.DragEvent, master: Master) => {
    e.dataTransfer.setData('application/master', JSON.stringify(master));
    e.dataTransfer.effectAllowed = 'move';
    setDraggingId(master.id);
  };

  const handleDragEnd = () => setDraggingId(null);

  if (masters.length === 0) {
    return (
      <div className="glass-panel p-4 text-center">
        <p className="text-white/30 text-sm">所有大师已入座</p>
        <p className="text-white/15 text-xs mt-1">点击圆桌上的大师可以移除</p>
      </div>
    );
  }

  return (
    <div className="glass-panel p-3 max-h-[500px] overflow-y-auto">
      <h3 className="text-white/60 text-xs font-medium mb-3 px-1 flex items-center gap-1.5">
        <span>🧑‍🤝‍🧑</span> 可选投资大师
        <span className="text-white/20 ml-auto">{masters.length}位</span>
      </h3>
      <div className="space-y-2">
        {masters.map(master => (
          <div
            key={master.id}
            draggable
            onDragStart={e => handleDragStart(e, master)}
            onDragEnd={handleDragEnd}
            onClick={() => setExpandedMaster(expandedMaster === master.id ? null : master.id)}
            className={`glass-panel-dark p-3 cursor-grab active:cursor-grabbing transition-all duration-200
              ${draggingId === master.id ? 'opacity-30 scale-95' : 'hover:border-amber-500/30 hover:scale-[1.02]'}
              ${expandedMaster === master.id ? 'border-amber-500/40' : ''}`}
          >
            <div className="flex items-center gap-2.5">
              <span className="text-2xl">{master.avatar_url}</span>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-white truncate">{master.name}</div>
                <div className="text-[10px] text-white/40 truncate">{master.title}</div>
              </div>
              <span className={`text-[9px] px-1.5 py-0.5 rounded border ${styleColors[master.style] || 'bg-white/10 text-white/50'}`}>
                {styleLabels[master.style] || master.style}
              </span>
            </div>

            {/* Expanded methodology */}
            {expandedMaster === master.id && (
              <div className="mt-2 pt-2 border-t border-white/10 text-xs text-white/50 leading-relaxed">
                {master.methodology}
                {master.best_for.length > 0 && (
                  <div className="mt-1.5 flex gap-1 flex-wrap">
                    <span className="text-white/30">擅长：</span>
                    {master.best_for.map(role => (
                      <span key={role} className="px-1.5 py-0.5 bg-white/5 rounded text-[10px] text-white/40">
                        {role}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Drag hint */}
            <div className="text-[9px] text-amber-400/50 mt-1.5 text-center">
              {expandedMaster === master.id ? '拖拽到圆桌座位' : '点击详情 | 拖拽入座'}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
