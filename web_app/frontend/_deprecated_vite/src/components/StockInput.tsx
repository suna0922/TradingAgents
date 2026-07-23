import { useState } from 'react';

interface StockInputProps {
  ticker: string;
  onTickerChange: (v: string) => void;
  onAnalyze: () => void;
  isAnalyzing: boolean;
  stockName: string;
}

const STOCK_SUGGESTIONS = [
  { code: '600519', name: '贵州茅台' },
  { code: '000858', name: '五粮液' },
  { code: '601318', name: '中国平安' },
  { code: '600036', name: '招商银行' },
  { code: '000333', name: '美的集团' },
  { code: '002415', name: '海康威视' },
  { code: '300750', name: '宁德时代' },
  { code: '600276', name: '恒瑞医药' },
  { code: '601012', name: '隆基绿能' },
  { code: '002594', name: '比亚迪' },
];

export default function StockInput({ ticker, onTickerChange, onAnalyze, isAnalyzing, stockName }: StockInputProps) {
  const [showSuggestions, setShowSuggestions] = useState(false);

  return (
    <div className="flex flex-col items-center gap-3">
      <div className="relative w-full max-w-xl">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <input
              type="text"
              className="input-stock w-full"
              placeholder="输入股票代码，例如：600519（贵州茅台）"
              value={ticker}
              onChange={e => onTickerChange(e.target.value)}
              onFocus={() => setShowSuggestions(true)}
              onBlur={() => setTimeout(() => setShowSuggestions(false), 200)}
              onKeyDown={e => { if (e.key === 'Enter') onAnalyze(); }}
              disabled={isAnalyzing}
            />
            {stockName && (
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-amber-400 font-medium text-lg">
                {stockName}
              </span>
            )}
          </div>
          <button
            onClick={onAnalyze}
            disabled={!ticker || isAnalyzing}
            className={`btn-primary whitespace-nowrap ${isAnalyzing ? 'opacity-60 cursor-not-allowed' : ''}`}
          >
            {isAnalyzing ? (
              <span className="flex items-center gap-2">
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                分析中...
              </span>
            ) : '🔍 开始分析'}
          </button>
        </div>

        {/* Suggestions dropdown */}
        {showSuggestions && !ticker && (
          <div className="absolute top-full left-0 right-0 mt-2 glass-panel p-2 z-50 max-h-60 overflow-y-auto">
            <p className="text-white/40 text-xs px-3 py-1">热门股票：</p>
            {STOCK_SUGGESTIONS.map(s => (
              <button
                key={s.code}
                className="w-full text-left px-3 py-2 rounded-lg hover:bg-white/10 text-white/70 hover:text-white transition-colors text-sm"
                onMouseDown={() => onTickerChange(s.code)}
              >
                <span className="font-mono text-amber-400">{s.code}</span>
                <span className="ml-2">{s.name}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
