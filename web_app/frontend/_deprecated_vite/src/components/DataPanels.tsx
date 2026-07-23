import { useState } from 'react';
import type { TechnicalIndicators, FundamentalsData } from '../types';

interface DataPanelsProps {
  technicals: TechnicalIndicators;
  fundamentals: FundamentalsData;
}

export default function DataPanels({ technicals, fundamentals }: DataPanelsProps) {
  const [activeTab, setActiveTab] = useState<'technicals' | 'fundamentals'>('technicals');

  return (
    <div className="glass-panel overflow-hidden">
      {/* Tab header */}
      <div className="flex border-b border-white/10">
        <button
          onClick={() => setActiveTab('technicals')}
          className={`flex-1 py-3 text-sm font-medium transition-colors ${activeTab === 'technicals' ? 'bg-amber-500/10 text-amber-400 border-b-2 border-amber-500' : 'text-white/50 hover:text-white/80'}`}
        >
          📈 技术面分析
        </button>
        <button
          onClick={() => setActiveTab('fundamentals')}
          className={`flex-1 py-3 text-sm font-medium transition-colors ${activeTab === 'fundamentals' ? 'bg-amber-500/10 text-amber-400 border-b-2 border-amber-500' : 'text-white/50 hover:text-white/80'}`}
        >
          📊 基本面分析
        </button>
      </div>

      {/* Content */}
      <div className="p-4 max-h-64 overflow-y-auto">
        {activeTab === 'technicals' ? (
          <TechnicalsView data={technicals} />
        ) : (
          <FundamentalsView data={fundamentals} />
        )}
      </div>
    </div>
  );
}

function TechnicalsView({ data }: { data: TechnicalIndicators }) {
  const priceUp = data.change_pct >= 0;
  const priceColor = priceUp ? 'text-red-400' : 'text-green-400';

  return (
    <div className="space-y-4">
      {/* Price header */}
      <div className="flex items-baseline gap-3">
        <span className="text-2xl font-bold">{data.latest_price.toFixed(2)}</span>
        <span className={`text-lg font-semibold ${priceColor}`}>
          {priceUp ? '+' : ''}{data.change_pct.toFixed(2)}%
        </span>
        <span className="text-white/30 text-sm">{data.analysis_date}</span>
      </div>

      {/* Indicator grids */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <IndicatorCard label="MA5" value={data.sma_5.toFixed(2)} />
        <IndicatorCard label="MA10" value={data.sma_10.toFixed(2)} />
        <IndicatorCard label="MA20" value={data.sma_20.toFixed(2)} />
        <IndicatorCard label="MA60" value={data.sma_60.toFixed(2)} />
        <IndicatorCard label="MACD" value={data.macd.toFixed(4)} />
        <IndicatorCard label="MACD Sig" value={data.macd_signal.toFixed(4)} />
        <IndicatorCard label="RSI(6)" value={data.rsi_6.toFixed(1)} />
        <IndicatorCard label="RSI(14)" value={data.rsi_14.toFixed(1)} />
        <IndicatorCard label="RSI(24)" value={data.rsi_24.toFixed(1)} />
      </div>

      {/* Bollinger */}
      <div className="bg-white/5 rounded-lg p-3 text-xs font-mono">
        <div className="flex justify-between">
          <span className="text-white/50">布林带</span>
          <span>上: {data.boll_upper.toFixed(2)} | 中: {data.boll_mid.toFixed(2)} | 下: {data.boll_lower.toFixed(2)}</span>
        </div>
      </div>

      {/* Additional */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <IndicatorCard label="ATR(14)" value={data.atr_14.toFixed(2)} />
        <IndicatorCard label="KDJ-K" value={data.kdj_k.toFixed(2)} />
        <IndicatorCard label="KDJ-D" value={data.kdj_d.toFixed(2)} />
        <IndicatorCard label="量比" value={data.volume_ratio.toFixed(2)} />
      </div>
    </div>
  );
}

function IndicatorCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white/5 rounded-lg px-3 py-2">
      <div className="text-white/40 text-xs mb-0.5">{label}</div>
      <div className="text-white font-mono text-sm">{value}</div>
    </div>
  );
}

function FundamentalsView({ data }: { data: FundamentalsData }) {
  return (
    <div className="space-y-4">
      <div className="flex items-baseline gap-3">
        <span className="text-xl font-bold text-amber-400">{data.stock_name}</span>
        <span className="text-white/30 text-sm">{data.ticker}</span>
        <span className="text-white/20 text-sm">报告期: {data.report_date}</span>
      </div>

      {data.sections.length > 0 ? (
        data.sections.map((section, i) => (
          <div key={i} className="bg-white/5 rounded-lg p-3">
            <h3 className="text-amber-400 font-medium text-sm mb-2">{section.title}</h3>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              {section.metrics.slice(0, 12).map((m, j) => (
                <div key={j} className="flex flex-col">
                  <span className="text-white/40 text-xs">{m.name}</span>
                  <span className="text-white font-mono text-sm">
                    {m.value.toLocaleString()}{m.unit}
                    {m.yoy !== null && (
                      <span className={`ml-1 text-xs ${m.yoy >= 0 ? 'text-red-400' : 'text-green-400'}`}>
                        ({m.yoy >= 0 ? '+' : ''}{m.yoy}%)
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))
      ) : (
        <div className="text-white/40 text-center py-8">基本面数据加载中...</div>
      )}
    </div>
  );
}
