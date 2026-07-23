import { useState } from 'react';
import type { Seat } from '../types';

interface TheoryEditorProps {
  seats: Seat[];
  onTheoryChange: (seatId: string, theory: string) => void;
  disabled?: boolean;
}

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

/**
 * 角色理论配置面板。
 *
 * 每个角色的 prompt 遵循「角色定义 + {自定义理论}」结构：
 * - 拖入大师 → 注入该大师的方法论
 * - 在这里填写自定义理论 → 注入你自己的投资理解（优先级高于大师）
 * - 两者都为空 → 使用角色原始 prompt
 */
export default function TheoryEditor({ seats, onTheoryChange, disabled }: TheoryEditorProps) {
  const [expandedSeat, setExpandedSeat] = useState<string | null>(null);

  return (
    <div className="glass-panel p-3 w-[380px]">
      <h3 className="text-white/60 text-xs font-medium mb-2 px-1 flex items-center gap-1.5">
        <span>🧩</span> 角色理论配置
        <span className="text-white/20 ml-auto text-[10px]">自定义理论 &gt; 大师方法论</span>
      </h3>
      <div className="space-y-1.5">
        {seats.map(seat => {
          const hasCustom = !!seat.custom_theory?.trim();
          const isOpen = expandedSeat === seat.id;
          return (
            <div key={seat.id} className="glass-panel-dark rounded-lg overflow-hidden">
              <button
                className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-white/5 transition-colors"
                onClick={() => setExpandedSeat(isOpen ? null : seat.id)}
              >
                <span className="text-base">{roleIcons[seat.role] || '💺'}</span>
                <span className="text-xs text-white/70 flex-1">{seat.label}</span>
                {/* 当前理论来源标签 */}
                {hasCustom ? (
                  <span className="text-[9px] px-1.5 py-0.5 rounded border bg-purple-500/20 text-purple-300 border-purple-500/30">
                    自定义理论
                  </span>
                ) : seat.master ? (
                  <span className="text-[9px] px-1.5 py-0.5 rounded border bg-amber-500/20 text-amber-300 border-amber-500/30">
                    {seat.master.avatar_url} {seat.master.name}
                  </span>
                ) : (
                  <span className="text-[9px] px-1.5 py-0.5 rounded border bg-white/5 text-white/30 border-white/10">
                    默认
                  </span>
                )}
                <span className="text-white/30 text-[10px]">{isOpen ? '▲' : '▼'}</span>
              </button>

              {isOpen && (
                <div className="px-2.5 pb-2">
                  <textarea
                    value={seat.custom_theory || ''}
                    disabled={disabled}
                    onChange={e => onTheoryChange(seat.id, e.target.value)}
                    placeholder={`为「${seat.label}」写下你自己的投资理论/分析框架…\n填写后将覆盖已拖入的大师方法论；留空则使用${seat.master ? `大师（${seat.master.name}）方法论` : '角色默认 prompt'}。`}
                    rows={5}
                    className="w-full text-xs bg-black/30 border border-white/10 rounded-md p-2 text-white/80
                               placeholder-white/20 focus:border-purple-400/50 focus:outline-none resize-y
                               disabled:opacity-40"
                  />
                  <div className="flex items-center justify-between mt-1">
                    <span className="text-[9px] text-white/25">
                      {hasCustom ? `${seat.custom_theory!.trim().length} 字 · 将以「自定义理论注入」块进入该角色 prompt` : '未填写'}
                    </span>
                    {hasCustom && (
                      <button
                        className="text-[9px] text-red-300/60 hover:text-red-300"
                        onClick={() => onTheoryChange(seat.id, '')}
                        disabled={disabled}
                      >
                        清空
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
