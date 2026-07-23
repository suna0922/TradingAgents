import type { ChatMessage } from '../types';
import React from 'react';

interface ChatPanelProps {
  messages: ChatMessage[];
  chatEndRef: React.RefObject<HTMLDivElement>;
}

const roleLabels: Record<string, string> = {
  bull: '看多',
  bear: '看空',
  fundamentals: '基本面',
  market: '技术面',
  risk_aggressive: '激进风控',
  risk_conservative: '保守风控',
  risk_neutral: '中立风控',
  manager: '投资经理',
};

const roleColors: Record<string, string> = {
  bull: 'border-red-500/30',
  bear: 'border-green-500/30',
  fundamentals: 'border-blue-500/30',
  market: 'border-amber-500/30',
  risk_aggressive: 'border-red-500/30',
  risk_conservative: 'border-green-500/30',
  risk_neutral: 'border-purple-500/30',
  manager: 'border-pink-500/30',
};

export default function ChatPanel({ messages, chatEndRef }: ChatPanelProps) {
  if (messages.length === 0) {
    return (
      <div className="glass-panel flex-1 flex items-center justify-center min-h-[300px]">
        <div className="text-center">
          <div className="text-4xl mb-3 opacity-40">🏛️</div>
          <p className="text-white/30 text-sm">圆桌讨论将在此展示</p>
          <p className="text-white/15 text-xs mt-1">输入股票代码并点击"开始分析"</p>
        </div>
      </div>
    );
  }

  return (
    <div className="glass-panel flex-1 flex flex-col min-h-[300px] overflow-hidden">
      {/* Header */}
      <div className="px-4 py-2.5 border-b border-white/10 flex items-center gap-2">
        <span className="text-sm text-white/60">📋 讨论记录</span>
        <span className="text-xs text-white/20">{messages.length} 条消息</span>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.map(msg => (
          <div
            key={msg.id}
            className={`animate-fadeIn border-l-2 pl-3 py-1.5 ${roleColors[msg.role] || 'border-white/10'}`}
          >
            {/* Header */}
            <div className="flex items-center gap-2 mb-1">
              <span className="text-lg">{msg.master_avatar}</span>
              <span className="text-sm font-medium text-white">{msg.master_name}</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-white/40">
                {roleLabels[msg.role] || msg.role}
              </span>
            </div>
            {/* Content */}
            <div className="text-sm text-white/70 whitespace-pre-wrap leading-relaxed">
              {msg.content}
            </div>
            {!msg.is_complete && (
              <span className="inline-block w-2 h-4 bg-amber-400 animate-pulse ml-1" />
            )}
          </div>
        ))}
        <div ref={chatEndRef} />
      </div>
    </div>
  );
}
