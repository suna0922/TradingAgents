import { useState, useCallback, useRef, useEffect } from 'react';
import type { Master, Seat, ChatMessage, TechnicalIndicators, FundamentalsData } from './types';
import StockInput from './components/StockInput';
import DataPanels from './components/DataPanels';
import RoundTable from './components/RoundTable';
import MasterPanel from './components/MasterPanel';
import TheoryEditor from './components/TheoryEditor';
import ChatPanel from './components/ChatPanel';
import ReportViewer from './components/ReportViewer';

const API_BASE = '/api';

function App() {
  const [ticker, setTicker] = useState('');
  const [stockName, setStockName] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [statusMessage, setStatusMessage] = useState('');

  // Data
  const [technicals, setTechnicals] = useState<TechnicalIndicators | null>(null);
  const [fundamentals, setFundamentals] = useState<FundamentalsData | null>(null);
  const [rawReportMd, setRawReportMd] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [signal, setSignal] = useState<string | null>(null);
  const [completedReports, setCompletedReports] = useState<string[]>([]);

  // Masters & Seats
  const [masters, setMasters] = useState<Master[]>([]);
  const [seats, setSeats] = useState<Seat[]>([]);
  const [availableMasters, setAvailableMasters] = useState<Master[]>([]);

  // Report viewer
  const [activeReport, setActiveReport] = useState<{ type: string; content: string } | null>(null);
  const [activeReportTab, setActiveReportTab] = useState<string | null>(null);

  // Chat panel scroll
  const chatEndRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Load masters & seats on mount
  useEffect(() => {
    fetch(`${API_BASE}/analyze/masters`).then(r => r.json()).then(data => {
      setMasters(data);
      setAvailableMasters(data);
    });
    fetch(`${API_BASE}/analyze/seats`).then(r => r.json()).then(setSeats);
  }, []);

  // Auto-scroll chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Start analysis
  const handleAnalyze = useCallback(async () => {
    if (!ticker || isAnalyzing) return;

    // Reset state
    setMessages([]);
    setSignal(null);
    setActiveReport(null);
    setActiveReportTab(null);
    setCompletedReports([]);
    setTechnicals(null);
    setFundamentals(null);
    setRawReportMd('');

    // Cancel previous
    eventSourceRef.current?.close();

    try {
      // Create session
      const sessionRes = await fetch(`${API_BASE}/analyze/session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker }),
      });
      const sessionData = await sessionRes.json();
      const sid = sessionData.session_id;
      setSessionId(sid);
      setStockName(sessionData.stock_name);

      // Update seats if masters assigned or custom theory written
      const configuredSeats = seats.filter(s => s.master || s.custom_theory?.trim());
      if (configuredSeats.length > 0) {
        await fetch(`${API_BASE}/analyze/session/${sid}/seats`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(configuredSeats),
        });
      }

      // Fetch data first
      setIsAnalyzing(true);
      setStatusMessage('正在获取数据...');

      const [techRes, fundRes] = await Promise.all([
        fetch(`${API_BASE}/stock/${ticker}/technicals`),
        fetch(`${API_BASE}/stock/${ticker}/fundamentals`),
      ]);

      if (techRes.ok) setTechnicals(await techRes.json());
      if (fundRes.ok) {
        const fData = await fundRes.json();
        setFundamentals(fData);
        setRawReportMd(fData.raw_report_md || '');
      }

      // Start SSE stream
      setStatusMessage('正在启动圆桌讨论...');

      const es = new EventSource(`${API_BASE}/analyze/session/${sid}/stream`);
      eventSourceRef.current = es;

      es.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === 'status') {
          setStatusMessage(data.message || '');
          if (data.signal) setSignal(data.signal);
        } else if (data.type === 'chat') {
          setMessages(prev => [...prev, data.message]);
        } else if (data.type === 'reports_ready') {
          setCompletedReports(data.reports || []);
        } else if (data.type === 'done') {
          setIsAnalyzing(false);
          setStatusMessage('分析完成！');
          es.close();
        } else if (data.type === 'error') {
          setStatusMessage(`错误: ${data.message}`);
          setIsAnalyzing(false);
          es.close();
        }
      };

      es.onerror = () => {
        setIsAnalyzing(false);
        es.close();
      };

    } catch (err: any) {
      setStatusMessage(`请求失败: ${err.message}`);
      setIsAnalyzing(false);
    }
  }, [ticker, isAnalyzing, seats]);

  // Cleanup on unmount
  useEffect(() => {
    return () => eventSourceRef.current?.close();
  }, []);

  // Load report content
  const loadReport = useCallback(async (type: string) => {
    if (!sessionId) return;
    if (activeReportTab === type) {
      setActiveReportTab(null);
      setActiveReport(null);
      return;
    }
    setActiveReportTab(type);
    try {
      const res = await fetch(`${API_BASE}/analyze/session/${sessionId}/report/${type}`);
      const data = await res.json();
      setActiveReport({ type, content: data.content || '报告尚未生成' });
    } catch {
      setActiveReport({ type, content: '加载失败' });
    }
  }, [sessionId, activeReportTab]);

  // Drag & Drop handlers
  const handleDropOnSeat = useCallback((seatId: string, master: Master) => {
    setSeats(prev => prev.map(seat =>
      seat.id === seatId
        ? { ...seat, master: { ...master } }
        : seat.master?.id === master.id
          ? { ...seat, master: null }
          : seat
    ));
    setAvailableMasters(prev => prev.filter(m => m.id !== master.id));
  }, []);

  const handleRemoveFromSeat = useCallback((seatId: string) => {
    setSeats(prev => {
      const seat = prev.find(s => s.id === seatId);
      if (seat?.master) {
        setAvailableMasters(prevMasters => {
          if (!prevMasters.find(m => m.id === seat.master!.id)) {
            return [...prevMasters, seat.master!];
          }
          return prevMasters;
        });
      }
      return prev.map(s => s.id === seatId ? { ...s, master: null } : s);
    });
  }, []);

  // 自定义理论编辑（优先级高于大师方法论）
  const handleTheoryChange = useCallback((seatId: string, theory: string) => {
    setSeats(prev => prev.map(s => s.id === seatId ? { ...s, custom_theory: theory } : s));
  }, []);

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="text-center py-6 border-b border-white/5">
        <h1 className="text-3xl font-bold bg-gradient-to-r from-amber-400 to-amber-200 bg-clip-text text-transparent">
          🏛️ 选股圆桌会议
        </h1>
        <p className="text-white/40 text-sm mt-1">投资大师围坐一堂，为你深度分析每一支股票</p>
      </header>

      {/* Stock Input */}
      <div className="px-6 py-5 flex justify-center">
        <StockInput
          ticker={ticker}
          onTickerChange={setTicker}
          onAnalyze={handleAnalyze}
          isAnalyzing={isAnalyzing}
          stockName={stockName}
        />
      </div>

      {/* Status */}
      {statusMessage && (
        <div className="px-6 pb-2 text-center">
          <span className={`inline-flex items-center gap-2 px-4 py-1.5 rounded-full text-sm
            ${isAnalyzing ? 'bg-amber-500/20 text-amber-300' : signal ? 'bg-green-500/20 text-green-300' : 'bg-white/10 text-white/60'}`}>
            {isAnalyzing && <span className="w-2 h-2 bg-amber-400 rounded-full animate-pulse" />}
            {statusMessage}
            {signal && (
              <span className={`ml-1 font-bold uppercase ${
                signal === 'buy' || signal === 'overweight' ? 'text-green-400' :
                signal === 'sell' || signal === 'underweight' ? 'text-red-400' : 'text-yellow-400'
              }`}>
                ({signal === 'buy' ? '买入' : signal === 'overweight' ? '增持' : 
                  signal === 'sell' ? '卖出' : signal === 'underweight' ? '减持' : '持有'})
              </span>
            )}
          </span>
        </div>
      )}

      {/* Data Panels */}
      {technicals && fundamentals && (
        <div className="px-6 pb-4">
          <DataPanels technicals={technicals} fundamentals={fundamentals} />
        </div>
      )}

      {/* Main Roundtable Section */}
      <div className="flex-1 px-6 pb-8">
        <div className="grid grid-cols-12 gap-4 h-full" style={{ minHeight: '500px' }}>
          {/* Left: Round Table + Theory Editor */}
          <div className="col-span-4 flex flex-col items-center justify-start gap-3">
            <RoundTable
              seats={seats}
              onDrop={handleDropOnSeat}
              onRemove={handleRemoveFromSeat}
            />
            <TheoryEditor
              seats={seats}
              onTheoryChange={handleTheoryChange}
              disabled={isAnalyzing}
            />
          </div>

          {/* Middle: Master Cards */}
          <div className="col-span-2">
            <MasterPanel masters={availableMasters} />
          </div>

          {/* Right: Chat + Reports */}
          <div className="col-span-6 flex flex-col gap-3">
            {/* Report Buttons */}
            <div className="flex gap-2 flex-wrap">
              {[
                { type: 'bull', label: '🐂 看多分析', color: 'bg-green-600/20 text-green-400 hover:bg-green-600/30 border-green-500/20' },
                { type: 'bear', label: '🐻 看空分析', color: 'bg-red-600/20 text-red-400 hover:bg-red-600/30 border-red-500/20' },
                { type: 'risk', label: '⚠️ 风险分析', color: 'bg-yellow-600/20 text-yellow-400 hover:bg-yellow-600/30 border-yellow-500/20' },
                { type: 'trading', label: '📊 交易分析', color: 'bg-blue-600/20 text-blue-400 hover:bg-blue-600/30 border-blue-500/20' },
                { type: 'decision', label: '🎯 决策结论', color: 'bg-purple-600/20 text-purple-400 hover:bg-purple-600/30 border-purple-500/20' },
              ].map(btn => (
                <button
                  key={btn.type}
                  onClick={() => loadReport(btn.type)}
                  disabled={!completedReports.includes(btn.type)}
                  className={`btn-report ${btn.color} ${
                    activeReportTab === btn.type ? 'ring-2 ring-white/30 scale-105' : ''
                  } ${!completedReports.includes(btn.type) ? 'opacity-40 cursor-not-allowed' : ''}`}
                >
                  {btn.label}
                  {completedReports.includes(btn.type) && <span className="ml-1">✓</span>}
                </button>
              ))}
            </div>

            {/* Report Viewer or Chat */}
            {activeReport ? (
              <ReportViewer
                type={activeReport.type}
                content={activeReport.content}
                onClose={() => { setActiveReport(null); setActiveReportTab(null); }}
              />
            ) : (
              <ChatPanel messages={messages} chatEndRef={chatEndRef} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
