
    const { useState, useEffect, useCallback, useRef } = React;

    const API_BASE = '/api';

    const MASTERS_DEFAULT = [
      { id: 'buffett', name: '沃伦·巴菲特', title: '价值投资之父', avatar: '👴', style: 'value', methodology: '寻找具有持久竞争优势、优秀管理层和合理估值的优质企业，长期持有。关注ROE、自由现金流、护城河。' },
      { id: 'graham', name: '本杰明·格雷厄姆', title: '证券分析之父', avatar: '📚', style: 'value', methodology: '寻找市场价格低于内在价值的股票，强调安全边际。关注PE、PB、股息率。' },
      { id: 'lynch', name: '彼得·林奇', title: '成长股猎手', avatar: '🔍', style: 'growth', methodology: '投资你了解的公司，寻找PEG<1的成长股。关注盈利增长率、市场份额扩张。' },
      { id: 'soros', name: '乔治·索罗斯', title: '宏观对冲大师', avatar: '🌐', style: 'macro', methodology: '利用反身性理论，识别市场极端情绪和趋势拐点。关注宏观经济、货币政策。' },
      { id: 'dalio', name: '瑞·达利欧', title: '全天候策略创始人', avatar: '🔄', style: 'macro', methodology: '理解经济机器运作原理，通过多元化配置穿越周期。关注债务周期、央行政策。' },
      { id: 'marks', name: '霍华德·马克斯', title: '周期与风险大师', avatar: '📉', style: 'contrarian', methodology: '理解市场周期，在恐惧时贪婪、贪婪时恐惧。关注信用利差、投资者情绪。' },
      { id: 'taleb', name: '纳西姆·塔勒布', title: '黑天鹅作者', avatar: '🦢', style: 'contrarian', methodology: '关注尾部风险和脆弱性，做多波动率。寻找被低估的风险。' },
      { id: 'simons', name: '詹姆斯·西蒙斯', title: '量化之王', avatar: '🔢', style: 'quant', methodology: '纯数据驱动，寻找统计套利和短期价格模式。关注价量关系、动量因子。' },
      { id: 'fisher', name: '菲利普·费雪', title: '成长股投资之父', avatar: '🌱', style: 'growth', methodology: '寻找具有卓越管理层、强大研发能力和高利润率的成长型公司。' },
      { id: 'burry', name: '迈克尔·伯里', title: '大空头原型', avatar: '🔮', style: 'contrarian', methodology: '深入挖掘财务报表中的异常，寻找市场定价错误的重大风险。' },
    ];

    const SEATS_DEFAULT = [
      // 📊 L1 分析层 — 并列发表报告
      { id: 'seat_fundamentals', role: 'fundamentals', label: '基本面分析师', desc: '分析财务数据', icon: '📊', phase: 'l1' },
      { id: 'seat_market',       role: 'market',       label: '技术面分析师', desc: '分析价格走势', icon: '📈', phase: 'l1' },
      // ⚔️ 辩论层 — 多空对坐
      { id: 'seat_bull',  role: 'bull', label: '看多分析师', desc: '寻找看多理由', icon: '🐂', phase: 'debate' },
      { id: 'seat_bear',  role: 'bear', label: '看空分析师', desc: '寻找看空风险', icon: '🐻', phase: 'debate' },
      // 💼 交易层 — 单独站
      { id: 'seat_trader', role: 'market', label: '交易策略师', desc: '制定交易方案', icon: '💼', phase: 'trader' },
      // 🛡️ 风控层 — 三人围坐
      { id: 'seat_risk_agg', role: 'risk_aggressive',  label: '激进风控师', desc: '评估上行风险回报', icon: '🔥',  phase: 'risk' },
      { id: 'seat_risk_con', role: 'risk_conservative', label: '保守风控师', desc: '评估下行最大亏损', icon: '🛡️', phase: 'risk' },
      { id: 'seat_risk_neu', role: 'risk_neutral',      label: '中立风控师', desc: '平衡风险与回报',    icon: '⚖️', phase: 'risk' },
      // 👑 决策层 — 独站压轴
      { id: 'seat_manager', role: 'manager', label: '投资组合经理', desc: '综合各方做出最终决策', icon: '👑', phase: 'pm' },
    ];

    const STYLE_LABELS = { value: '价值', growth: '成长', momentum: '趋势', quant: '量化', macro: '宏观', contrarian: '逆向' };
    const STYLE_COLORS = { value: 'bg-blue-500/20 text-blue-300 border-blue-500/30', growth: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30', momentum: 'bg-amber-500/20 text-amber-300 border-amber-500/30', quant: 'bg-purple-500/20 text-purple-300 border-purple-500/30', macro: 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30', contrarian: 'bg-rose-500/20 text-rose-300 border-rose-500/30' };
    const ROLE_LABELS = {
      bull: '看多', bear: '看空',
      fundamentals: '基本面', market: '技术面', news: '新闻', sentiment: '情绪',
      risk_aggressive: '激进风控', risk_conservative: '保守风控', risk_neutral: '中立风控',
      research_manager: '研究主管', trader: '交易策略师', portfolio_manager: '投资组合经理'
    };
    const ROLE_STYLE = {
      bull:              { bg: 'bg-red-950/20', border: 'border-red-500/30', badge: 'bg-red-500/20 text-red-300', emoji: '🐂', label: '看多分析师' },
      bear:              { bg: 'bg-green-950/20', border: 'border-green-500/30', badge: 'bg-green-500/20 text-green-300', emoji: '🐻', label: '看空分析师' },
      fundamentals:      { bg: 'bg-blue-950/20', border: 'border-blue-500/30', badge: 'bg-blue-500/20 text-blue-300', emoji: '📊', label: '基本面分析师' },
      market:            { bg: 'bg-cyan-950/20', border: 'border-cyan-500/30', badge: 'bg-cyan-500/20 text-cyan-300', emoji: '📈', label: '技术面分析师' },
      news:              { bg: 'bg-slate-800/40', border: 'border-slate-500/30', badge: 'bg-slate-500/20 text-slate-300', emoji: '📰', label: '新闻分析师' },
      sentiment:         { bg: 'bg-pink-950/20', border: 'border-pink-500/30', badge: 'bg-pink-500/20 text-pink-300', emoji: '💬', label: '情绪面分析师' },
      risk_aggressive:   { bg: 'bg-red-950/20', border: 'border-red-500/30', badge: 'bg-red-500/20 text-red-300', emoji: '🔥', label: '激进风控师' },
      risk_conservative: { bg: 'bg-green-950/20', border: 'border-green-500/30', badge: 'bg-green-500/20 text-green-300', emoji: '🛡️', label: '保守风控师' },
      risk_neutral:      { bg: 'bg-purple-950/20', border: 'border-purple-500/30', badge: 'bg-purple-500/20 text-purple-300', emoji: '⚖️', label: '中立风控师' },
      research_manager:  { bg: 'bg-amber-950/20', border: 'border-amber-500/30', badge: 'bg-amber-500/20 text-amber-300', emoji: '🔬', label: '研究主管' },
      trader:            { bg: 'bg-blue-950/20', border: 'border-blue-500/30', badge: 'bg-blue-500/20 text-blue-300', emoji: '💼', label: '交易策略师' },
      portfolio_manager: { bg: 'bg-amber-950/20', border: 'border-amber-500/30', badge: 'bg-amber-500/20 text-amber-300', emoji: '👑', label: '投资组合经理' },
    };
    const ROLE_BORDERS = { bull: 'border-red-500/30', bear: 'border-green-500/30', fundamentals: 'border-blue-500/30', market: 'border-amber-500/30', risk_aggressive: 'border-red-500/30', risk_conservative: 'border-green-500/30', risk_neutral: 'border-purple-500/30', manager: 'border-pink-500/30' };
    const REPORT_BTNS = [
      { type: 'fundamentals', label: '📊 基本面报告', cls: 'bg-blue-800/30 text-blue-300 border-blue-500/20 hover:bg-blue-800/40' },
      { type: 'technical', label: '📈 技术面报告', cls: 'bg-amber-800/30 text-amber-300 border-amber-500/20 hover:bg-amber-800/40' },
      { type: 'bull', label: '🐂 看多分析', cls: 'bg-green-800/30 text-green-300 border-green-500/20 hover:bg-green-800/40' },
      { type: 'bear', label: '🐻 看空分析', cls: 'bg-red-800/30 text-red-300 border-red-500/20 hover:bg-red-800/40' },
      { type: 'trading', label: '💼 交易策略', cls: 'bg-cyan-800/30 text-cyan-300 border-cyan-500/20 hover:bg-cyan-800/40' },
      { type: 'risk', label: '🛡️ 风控报告', cls: 'bg-orange-800/30 text-orange-300 border-orange-500/20 hover:bg-orange-800/40' },
      { type: 'decision', label: '🎯 最终决策', cls: 'bg-purple-800/30 text-purple-300 border-purple-500/20 hover:bg-purple-800/40' },
    ];
    const STOCK_SUGGESTIONS = [
      { code: '600519', name: '贵州茅台' }, { code: '000858', name: '五粮液' }, { code: '601318', name: '中国平安' },
      { code: '000333', name: '美的集团' }, { code: '300750', name: '宁德时代' }, { code: '002594', name: '比亚迪' },
    ];
    const ROLE_ICONS = { bull: '🐂', bear: '🐻', fundamentals: '📊', market: '📈', risk_aggressive: '🔥', risk_conservative: '🛡️', risk_neutral: '⚖️', manager: '👑', custom: '🎭' };
    const SEAT_ANGLES_BASE = [300, 345, 30, 60, 120, 180, 210, 240];

    // ── Utility: extract conclusion summary from agent message ──
    function _stripMD(s) {
      return s.replace(/\*{1,3}/g, '').replace(/^#{1,3}\s*/, '').replace(/^[\d一二三四五六七八九十]+[\.\、\s)]+/, '').trim();
    }

    function extractSummary(content, role) {
      if (!content) return '(无内容)';
      const lines = content.split('\n').map(l => l.trim()).filter(Boolean);

      // 角色 → [关键词, 当行同时含某评级词才采纳]
      const rules = {
        bull:            { kw: ['看多评级', '看多结论', '多头结论'], need: /强烈看多|看多|谨慎看多/ },
        bear:            { kw: ['看空评级', '看空结论', '空头结论'], need: /强烈看空|看空|谨慎看空/ },
        fundamentals:    { kw: ['总体评价'], need: /满分\d+/ },
        market:          { kw: ['总体评价'], need: /满分\d+/ },
        risk_aggressive: { kw: ['风险评级', '上行风险'], need: /高风险|中风险|低风险|极高风险/ },
        risk_conservative:{ kw: ['风险评级', '下行风险'], need: /高风险|中风险|低风险|极高风险/ },
        risk_neutral:    { kw: ['风险评级', '最终风险评级'], need: /高风险|中风险|低风险|极高/ },
        trader:          { kw: ['交易方向', '方向'], need: /做多|做空|观望|买入|卖出/ },
        manager:         { kw: ['投资建议', '最终建议', '决策建议'], need: /买入|增持|持有|减持|卖出/ },
      };
      const rule = rules[role] || { kw: [], need: /.*/ };

      // 1) 精确匹配：行含角色关键词 + 同时含评级/方向词
      for (const line of lines) {
        const hasKW = rule.kw.some(k => line.includes(k));
        const hasNeed = rule.need.test(line);
        if (hasKW && hasNeed && line.length <= 120) {
          return _stripMD(line).substring(0, 60);
        }
      }

      // 2) 次选：含角色关键词的短行（不论有无评级词）
      for (const line of lines) {
        if (rule.kw.some(k => line.includes(k)) && line.length <= 80) {
          return _stripMD(line).substring(0, 60);
        }
      }

      // 3) 兜底：评分行或明确评级行
      for (const line of lines) {
        if (line.match(/(满分\d+|评分[：:]\s*\d+|评级[：:]\s*(高|中|低|极)|强烈(看多|看空|买入|卖出)|(看多评级|看空评级|做多|做空|买入|卖出|增持|减持|持有))/)) {
          return _stripMD(line).substring(0, 60);
        }
      }
      return '(待分析)';
    }

    // ── App ──
    function App() {
      const [ticker, setTicker] = useState('');
      const [stockName, setStockName] = useState('');
      const [sessionId, setSessionId] = useState('');
      const [isAnalyzing, setIsAnalyzing] = useState(false);
      const [statusMsg, setStatusMsg] = useState('');
      const [technicals, setTechnicals] = useState(null);
      const [fundamentals, setFundamentals] = useState(null);
      const [messages, setMessages] = useState([]);
      const [signal, setSignal] = useState(null);
      const [completedReports, setCompletedReports] = useState([]);
      const [seats, setSeats] = useState(SEATS_DEFAULT.map(s => ({ ...s, master: null })));
      const [activeReport, setActiveReport] = useState(null);
      const [activeReportTab, setActiveReportTab] = useState(null);
      const [tradingRules, setTradingRules] = useState([]);  // P0: structured rules
      const [showAddSeat, setShowAddSeat] = useState(false);
      const [newSeatLabel, setNewSeatLabel] = useState('');
      const [newSeatIcon, setNewSeatIcon] = useState('🎭');
      const [editingSeatId, setEditingSeatId] = useState(null);
      const [masters, setMasters] = useState(MASTERS_DEFAULT);
      const [theoryModalOpen, setTheoryModalOpen] = useState(false);
      const [theoryMode, setTheoryMode] = useState('custom'); // 'custom' | 'master'
      const chatEndRef = useRef(null);
      const esRef = useRef(null);
      // 不自动滚到底，用户自行滚动
      
      // Fetch masters from API on mount
      useEffect(() => {
        fetch(`${API_BASE}/analyze/masters`).then(r => r.json()).then(data => {
          if (data && data.length > 0) setMasters(data);
        }).catch(() => {});
      }, []);

      const handleAnalyze = useCallback(async () => {
        if (!ticker || isAnalyzing) return;
        // 立即锁定，防止重复点击
        setIsAnalyzing(true); setStatusMsg('正在准备分析...');
        setMessages([]); setSignal(null); setActiveReport(null); setActiveReportTab(null);
        setTradingRules([]);  // P0: clear old rules
        setCompletedReports([]);
        esRef.current?.close();

        try {
          const sr = await fetch(`${API_BASE}/analyze/session`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker }),
          });
          if (!sr.ok) {
            const errText = await sr.text().catch(() => '');
            throw new Error(`创建会话失败 (${sr.status})${errText ? ': ' + errText.slice(0, 200) : ''}`);
          }
          const raw = await sr.text();
          if (!raw) throw new Error('服务器返回空响应，请刷新后重试');
          const sd = JSON.parse(raw);
          setSessionId(sd.session_id);
          setStockName(sd.stock_name);

          // ★ 数据面板：直接走独立、可靠的 REST 接口，与 LLM 分析流完全解耦。
          //   setTimeout(10) 把 setState 踢出 React 18 批处理，确保面板立即渲染。
          setStatusMsg('正在加载行情与财务数据...');
          fetch(`${API_BASE}/stock/${ticker}/technicals`)
            .then(r => r.ok ? r.json() : null)
            .then(d => { if (d) setTimeout(() => setTechnicals(d), 10); })
            .catch(() => {});
          fetch(`${API_BASE}/stock/${ticker}/fundamentals`)
            .then(r => r.ok ? r.json() : null)
            .then(d => { if (d) setTimeout(() => setFundamentals(d), 10); })
            .catch(() => {});

          // 发送已配置的座位：拖入大师 或 填写了自定义理论（自定义理论优先级更高）
          const configuredSeats = seats.filter(s => (s.master || (s.custom_theory || '').trim()) && s.role !== 'custom');
          if (configuredSeats.length > 0) {
            const seatRes = await fetch(`${API_BASE}/analyze/session/${sd.session_id}/seats`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(configuredSeats),
            });
            if (!seatRes.ok) {
              const et = await seatRes.text().catch(() => '');
              throw new Error(`设置专家失败 (${seatRes.status})${et ? ': ' + et.slice(0, 200) : ''}`);
            }
          }

          setStatusMsg('正在启动圆桌讨论...');
          const es = new EventSource(`${API_BASE}/analyze/session/${sd.session_id}/stream`);
          esRef.current = es;
          es.addEventListener('message', (event) => {
            let d;
            try { d = JSON.parse(event.data); } catch { return; }
            console.log('[SSE]', d.type);
            if (d.type === 'status') { setStatusMsg(d.message || ''); if (d.signal) setSignal(d.signal); }
            else if (d.type === 'chat') setMessages(prev => [...prev, d.message]);
            else if (d.type === 'data_technicals') { console.log('[SSE] tech data received'); setTechnicals(prev => prev || d.data); }
            else if (d.type === 'data_fundamentals') { console.log('[SSE] fund data received'); setFundamentals(prev => (prev && prev.sections?.length) ? prev : d.data); }
            else if (d.type === 'reports_ready') setCompletedReports(d.reports || []);
            else if (d.type === 'done') { setIsAnalyzing(false); setStatusMsg('分析完成！'); es.close(); 
              // P0: fetch structured rules on completion
              fetch(`${API_BASE}/analyze/session/${sd.session_id}/rules`).then(r => r.json()).then(data => { if (data?.rules) setTradingRules(data.rules); }).catch(() => {}); }
            else if (d.type === 'error') { setStatusMsg(`⚠️ ${d.message}`); setIsAnalyzing(false); es.close(); }
            else console.log('[SSE] unknown type:', d.type);
          });
          es.addEventListener('error', () => { setStatusMsg('⚠️ 连接中断，请刷新后重试'); setIsAnalyzing(false); es.close(); });
        } catch (err) { setStatusMsg(`请求失败: ${err.message}`); setIsAnalyzing(false); }
      }, [ticker, isAnalyzing, seats]);

      useEffect(() => () => esRef.current?.close(), []);

      const loadReport = useCallback(async (type) => {
        if (!sessionId) return;
        if (activeReportTab === type) { setActiveReportTab(null); setActiveReport(null); return; }
        setActiveReportTab(type);
        try {
          const res = await fetch(`${API_BASE}/analyze/session/${sessionId}/report/${type}`);
          const data = await res.json();
          setActiveReport({ type, content: data.content || '报告尚未生成' });
        } catch { setActiveReport({ type, content: '加载失败' }); }
      }, [sessionId, activeReportTab]);

      // ticker 清空时重置数据面板（数据加载改由 handleAnalyze 直连 REST，SSE 仅作补充）
      useEffect(() => { if (!ticker) { setTechnicals(null); setFundamentals(null); } }, [ticker]);

      // Master stays available (reusable)
      const handleDropOnSeat = useCallback((seatId, master) => {
        setSeats(prev => prev.map(s => s.id === seatId ? { ...s, master: { ...master } } : s));
      }, []);

      const handleRemoveFromSeat = useCallback((seatId) => {
        setSeats(prev => prev.map(s => s.id === seatId ? { ...s, master: null } : s));
      }, []);

      // Custom seat management
      const addSeat = () => {
        if (!newSeatLabel.trim()) return;
        const id = 'seat_custom_' + Date.now();
        setSeats(prev => [...prev, { id, role: 'custom', label: newSeatLabel.trim(), desc: '自定义角色', icon: newSeatIcon, master: null }]);
        setNewSeatLabel(''); setNewSeatIcon('🎭'); setShowAddSeat(false);
      };
      const removeSeat = (seatId) => {
        setSeats(prev => prev.filter(s => s.id !== seatId));
      };
      const renameSeat = (seatId, newLabel) => {
        setSeats(prev => prev.map(s => s.id === seatId ? { ...s, label: newLabel } : s));
        setEditingSeatId(null);
      };

      // 自定义理论编辑：非空时覆盖该角色的大师方法论注入
      const handleTheoryChange = useCallback((seatId, theory) => {
        setSeats(prev => prev.map(s => s.id === seatId ? { ...s, custom_theory: theory } : s));
      }, []);

      const sigLabel = signal === 'buy' ? '买入' : signal === 'overweight' ? '增持' : signal === 'sell' ? '卖出' : signal === 'underweight' ? '减持' : '持有';
      const sigColor = signal === 'buy' || signal === 'overweight' ? 'text-red-400' : signal === 'sell' || signal === 'underweight' ? 'text-green-400' : 'text-yellow-400';
      const iconOptions = ['🐂','🐻','📊','📈','⚖️','👑','🔥','🛡️','🎭','💡','🎯','🧠','👁️','💎','🚀','⚠️','🔮','💼'];

      return (
        <div className="min-h-screen">
          {/* Compact Header + Input — single line */}
          <div className="px-6 py-3 flex items-center gap-3 border-b border-white/5">
            <h1 className="flex-shrink-0 text-lg font-bold" style={{background: 'linear-gradient(135deg, #f59e0b, #fde68a)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent'}}>🏛️ 选股圆桌会议</h1>
            <span className="text-white/10 text-[10px] flex-shrink-0 hidden sm:inline">v.444</span>
            <span className="text-white/20 text-xs flex-shrink-0 hidden lg:inline">将大师理念注入每个分析环节</span>
            <div className="flex items-center gap-2 ml-auto" style={{width: '440px'}}>
              <div className="relative" style={{flex: 1}}>
                <input type="text" className="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white placeholder-white/30 text-sm"
                  style={{outline: 'none', cursor: 'text'}}
                  placeholder="输入股票代码，例如：600519" value={ticker}
                  onChange={e => setTicker(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleAnalyze()} disabled={isAnalyzing} />
                {stockName && <span className="absolute right-3 top-1/2 -translate-y-1/2 text-amber-400 font-semibold text-sm pointer-events-none">{stockName}</span>}
              </div>
              <button onClick={handleAnalyze} disabled={!ticker || isAnalyzing} className="btn-primary text-sm whitespace-nowrap px-5 py-2">
                {isAnalyzing ? <span className="flex items-center gap-1.5"><span className="spinner"/>分析中</span> : '🔍 开始分析'}
              </button>
            </div>
          </div>
          {/* Suggestions — show when no ticker entered */}
          {!ticker && (
            <div className="px-6 pb-2 flex gap-1.5 flex-wrap justify-center">
              {STOCK_SUGGESTIONS.map(s => (
                <button key={s.code} onClick={() => setTicker(s.code)} className="px-2 py-0.5 bg-white/5 rounded text-white/35 hover:text-white hover:bg-white/10 text-xs transition-colors">{s.code} {s.name}</button>
              ))}
            </div>
          )}

          {/* Status */}
          {statusMsg && (
            <div className="px-6 pb-2 text-center">
              <span className={`inline-flex items-center gap-2 px-4 py-1.5 rounded-full text-sm ${isAnalyzing ? 'bg-amber-500/20 text-amber-300' : signal ? 'bg-green-500/20 text-green-300' : 'bg-white/10 text-white/60'}`}>
                {isAnalyzing && <span className="w-2 h-2 bg-amber-400 rounded-full animate-pulse"/>}
                {statusMsg}
                {signal && <span className={`ml-1 font-bold ${sigColor}`}>· PM建议：{sigLabel}</span>}
              </span>
            </div>
          )}

          {/* Data Panels — always visible */}
          <div className="px-6 pb-4">
            <DataPanels technicals={technicals} fundamentals={fundamentals} ticker={ticker} isAnalyzing={isAnalyzing} />
          </div>

          {/* Main Section — Left: 圆桌+大师 | Right: 对话+报告 */}
          <div className="px-6 pb-8">
            <div className="flex gap-4">
              {/* ── 左侧: 角色圆桌 ── */}
              <div className="w-[520px] flex-shrink-0 flex flex-col gap-4">
                <div className="glass-panel p-3 flex justify-center">
                  <RoundTable seats={seats} onDrop={handleDropOnSeat} onRemove={handleRemoveFromSeat} onRename={renameSeat} onRemoveSeat={removeSeat}
                    editingSeatId={editingSeatId} setEditingSeatId={setEditingSeatId} />
                </div>
                <button onClick={() => { setTheoryModalOpen(true); }}
                  className="w-full py-2 text-xs font-medium text-white/70 hover:text-amber-300 bg-white/5 border border-white/10 rounded-lg hover:border-amber-500/30 transition-all">👥 大师理论配置</button>
                <div className="glass-panel p-2.5 flex-shrink-0">
                  {!showAddSeat ? (
                    <button onClick={() => setShowAddSeat(true)} className="w-full py-2 text-xs text-white/40 hover:text-amber-400 border border-dashed border-white/8 rounded-lg hover:border-amber-500/25 transition-colors">＋ 添加自定义角色</button>
                    ) : (
                      <div className="space-y-2">
                        <div className="flex gap-1 flex-wrap">{iconOptions.map(ic => (
                          <button key={ic} onClick={() => setNewSeatIcon(ic)} className={`text-lg p-0.5 rounded ${newSeatIcon === ic ? 'bg-amber-500/30' : 'hover:bg-white/10'}`}>{ic}</button>
                        ))}</div>
                        <input type="text" className="w-full px-2 py-1.5 bg-white/10 border border-white/20 rounded-lg text-white text-xs placeholder-white/30 focus:border-amber-500/30"
                          placeholder="角色名称" value={newSeatLabel} onChange={e => setNewSeatLabel(e.target.value)} onKeyDown={e => e.key === 'Enter' && addSeat()} />
                        <div className="flex gap-1">
                          <button onClick={addSeat} className="flex-1 py-1 bg-amber-600/40 text-amber-300 rounded text-xs hover:bg-amber-600/60">添加</button>
                          <button onClick={() => setShowAddSeat(false)} className="py-1 px-2 bg-white/10 text-white/40 rounded text-xs hover:bg-white/20">取消</button>
                        </div>
                      </div>
                    )}
                  </div>
              </div>


              {/* ── 右侧: 分析对话 + 报告 ── */}
              <div className="flex-1 flex flex-col gap-3 min-w-0">
                <div className="flex gap-1.5 flex-wrap">
                  {REPORT_BTNS.map(btn => (
                    <button key={btn.type} onClick={() => loadReport(btn.type)} disabled={!completedReports.includes(btn.type)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${btn.cls} ${!completedReports.includes(btn.type) ? 'opacity-30 cursor-not-allowed' : ''} ${activeReportTab === btn.type ? 'ring-2 ring-white/30' : ''}`}>
                      {btn.label}{completedReports.includes(btn.type) ? ' ✓' : ''}
                    </button>
                  ))}
                </div>
                {activeReport ? (
                  <ReportViewer type={activeReport.type} content={activeReport.content} onClose={() => { setActiveReport(null); setActiveReportTab(null); }} />
                ) : (
                  <PhasePanel messages={messages} chatEndRef={chatEndRef} />
                )}
                {/* P0: Trading Rules Panel */}
                {tradingRules.length > 0 && <RulesPanel rules={tradingRules} signal={signal} />}
              </div>
            </div>
          </div>
          {theoryModalOpen && <TheoryModal seats={seats} masters={masters}
            onClose={() => setTheoryModalOpen(false)}
            onTheoryChange={handleTheoryChange}
            onMasterAssign={(seatId, master) => { setSeats(seats.map(s => s.id === seatId ? { ...s, master } : s)); handleTheoryChange(seatId, master.methodology || ''); }} />}
        </div>
      );
    }

    // ── DataPanels ──
    function DataPanels({ technicals, fundamentals, ticker, isAnalyzing }) {
      const [tab, setTab] = useState('technicals');
      const hasData = technicals || fundamentals;
      const isWaiting = isAnalyzing && ticker && ticker.length >= 6 && !hasData;
      return (
        <div className="glass-panel overflow-hidden">
          <div className="flex border-b border-white/10">
            {[{ k: 'technicals', label: '📈 技术面分析' }, { k: 'fundamentals', label: '📊 基本面分析' }].map(t => (
              <button key={t.k} onClick={() => setTab(t.k)}
                className={`flex-1 py-3 text-sm font-medium ${tab === t.k ? 'bg-amber-500/10 text-amber-400 border-b-2 border-amber-500' : 'text-white/50 hover:text-white/80'}`}>{t.label}</button>
            ))}
          </div>
          <div className="p-4 max-h-72 overflow-y-auto">
            {isWaiting ? (
              <div className="text-center text-white/30 py-6">
                <p className="text-sm"><span className="spinner mr-2" style={{width:'12px',height:'12px',borderWidth:'1.5px'}}/>正在加载数据...</p>
              </div>
            ) : !hasData ? (
              <div className="text-center text-white/30 py-6">
                <p className="text-sm">{ticker ? `已输入 ${ticker}，等待数据推送…` : '输入股票代码并点击「开始分析」'}</p>
                <p className="text-xs mt-1 opacity-50">technical: {technicals ? '✓' : '✗'} | fundamental: {fundamentals ? '✓' : '✗'} | v.444</p>
              </div>
            ) : tab === 'technicals' ? <TechnicalsView data={technicals} /> : <FundamentalsView data={fundamentals} ticker={ticker} />
          }
          </div>
        </div>
      );
    }

    function TechnicalsView({ data }) {
      if (!data) return <div className="text-white/40 text-center py-4">加载中...</div>;
      const up = data.change_pct >= 0;
      return (
        <div className="space-y-4">
          <div className="flex items-baseline gap-3">
            <span className="text-2xl font-bold">¥{data.latest_price?.toFixed(2)}</span>
            <span className={`text-lg font-semibold ${up ? 'text-red-400' : 'text-green-400'}`}>{up ? '+' : ''}{data.change_pct?.toFixed(2)}%</span>
            <span className="text-white/30 text-sm">{data.analysis_date}</span>
          </div>
          <div className="bg-white/5 rounded-lg p-3">
            <div className="grid grid-cols-3 gap-2">
            {[['MA5', data.sma_5], ['MA10', data.sma_10], ['MA20', data.sma_20], ['MA60', data.sma_60],
              ['MACD', data.macd], ['RSI(14)', data.rsi_14], ['KDJ-K', data.kdj_k], ['KDJ-D', data.kdj_d],
              ['ATR14', data.atr_14], ['量比', data.volume_ratio],
              ['市盈率', data.pe_static, '倍'], ['市净率', data.pb, '倍'], ['PEG', data.peg, ''],
              ['总市值', data.market_cap, '亿'], ['市销率', data.ps, '倍'], ['股息率', data.dividend_yield, '%']].map(([l, v, u]) => (
              <div key={l} className="relative">
                <div className="text-white/40 text-xs">{l}</div>
                <div className="text-white font-mono text-sm">{typeof v === 'number' ? v.toFixed(2) + (u || '') : '--'}</div>
              </div>
            ))}
          </div>
          </div>
        </div>
      );
    }

    // ── Mini SVG line chart for hover tooltip ──
    function MiniLineChart({ values, periods, label, width, height }) {
      if (!values || values.length < 2) return <div className="text-white/30 text-xs">数据不足</div>;
      const w = width || 180, h = height || 80;
      const pad = { top: 12, right: 8, bottom: 16, left: 8 };
      const chartW = w - pad.left - pad.right, chartH = h - pad.top - pad.bottom;
      const validVals = values.filter(v => v != null);
      if (validVals.length < 2) return <div className="text-white/30 text-xs">数据不足</div>;
      const min = Math.min(...validVals), max = Math.max(...validVals);
      const range = max - min || 1;
      const points = values.map((v, i) => {
        if (v == null) return null;
        const x = pad.left + (i / (values.length - 1)) * chartW;
        const y = pad.top + chartH - ((v - min) / range) * chartH;
        return { x, y, v, period: periods[i] || '' };
      }).filter(p => p != null);
      const pathD = points.map((p, i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ',' + p.y.toFixed(1)).join(' ');
      return (
        <div>
          <div className="text-white/60 text-xs mb-1 font-medium">{label}</div>
          <svg width={w} height={h} className="bg-black/30 rounded">
            <path d={pathD} fill="none" stroke="#f59e0b" strokeWidth="1.5" />
            {points.map((p, i) => (
              <g key={i}>
                <circle cx={p.x} cy={p.y} r="2.5" fill="#f59e0b" />
                <text x={p.x} y={h - 3} textAnchor="middle" fill="#ffffff60" fontSize="7">
                  {p.period ? p.period.substring(0, 7) : ''}
                </text>
              </g>
            ))}
            <text x="3" y="10" fill="#ffffff40" fontSize="7">{max.toFixed(1)}</text>
            <text x="3" y={pad.top + chartH} fill="#ffffff40" fontSize="7">{min.toFixed(1)}</text>
          </svg>
        </div>
      );
    }

    // ── Metric card with hover chart ──
    function MetricCard({ name, value, values, periods, unit }) {
      const [showChart, setShowChart] = useState(false);
      const hasHistory = values && values.length > 0;
      // ★ 切换年报/季报时主数字必须跟着变：
      //   有历史数据 → 显示历史最新期值（年报=去年年报，季报=最近季报）
      //   无历史数据（PE/PB/市值等估值指标）→ 显示当前快照值
      const current = hasHistory ? values[values.length - 1] : value;
      // 最新一期标签
      const latestPeriod = hasHistory && periods && periods.length > 0
        ? periods[periods.length - 1] : null;
      const periodLabel = latestPeriod
        ? (latestPeriod.includes('12-31')
            ? latestPeriod.substring(0, 4) + '年报'
            : latestPeriod.substring(0, 7).replace('-', 'Q'))
        : '最新';
      // Format the short period labels: "2025-12-31" → "2025"
      const shortPeriods = periods ? periods.map(p => typeof p === 'string' ? p.substring(0, 4) : p) : [];
      const display = current != null
        ? (typeof current === 'number' ? current.toLocaleString() : current) + (unit || '')
        : '--';
      return (
        <div className="relative"
          onMouseEnter={() => hasHistory && values.length > 1 && setShowChart(true)}
          onMouseLeave={() => setShowChart(false)}>
          <div className="text-white/40 text-xs">{name}</div>
          <div className="text-white font-mono text-sm">{display}</div>
          {showChart && hasHistory && values.length > 1 && (
            <div className="absolute z-50 bottom-full left-0 mb-2 p-2 bg-gray-900/95 border border-amber-500/30 rounded-lg shadow-xl"
              style={{minWidth: '200px'}}>
              <MiniLineChart values={values} periods={shortPeriods} label={name + (unit ? '(' + unit + ')' : '')} width={200} height={90} />
            </div>
          )}
        </div>
      );
    }

    function FundamentalsView({ data, ticker }) {
      const [historyType, setHistoryType] = useState('annual');
      const [history, setHistory] = useState(null);
      const [loadingHistory, setLoadingHistory] = useState(false);

      useEffect(() => {
        if (!ticker) return;
        setLoadingHistory(true);
        fetch(`${API_BASE}/stock/${ticker}/fundamentals/history?type=${historyType}`)
          .then(r => r.json())
          .then(d => { setHistory(d); setLoadingHistory(false); })
          .catch(() => setLoadingHistory(false));
      }, [ticker, historyType]);

      const stockName = data?.stock_name || history?.stock_name || '';
      return (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-base font-bold text-amber-400">{stockName} {ticker}</span>
            <div className="flex bg-white/5 rounded-lg p-0.5">
              <button onClick={() => setHistoryType('annual')}
                className={`px-3 py-1 text-xs rounded-md ${historyType==='annual'?'bg-amber-500/30 text-amber-300':'text-white/40'}`}>年报</button>
              <button onClick={() => setHistoryType('quarterly')}
                className={`px-3 py-1 text-xs rounded-md ${historyType==='quarterly'?'bg-amber-500/30 text-amber-300':'text-white/40'}`}>季报</button>
            </div>
          </div>
          {loadingHistory ? (
            <div className="text-center text-white/20 text-xs py-4">加载中...</div>
          ) : history?.metrics && Object.keys(history.metrics).length > 0 ? (
            <div className="bg-white/5 rounded-lg p-3">
              <div className="grid grid-cols-3 gap-2">
                {Object.entries(history.metrics).map(([name, values], j) => (
                  <MetricCard key={j} name={name} values={values} periods={history.periods} unit="" />
                ))}
              </div>
            </div>
          ) : (
            <div className="text-white/40 text-center py-4">暂无{historyType==='annual'?'年报':'季报'}数据</div>
          )}
        </div>
      );
    }

    // ── RoundTable (compact vertical stage flow for left column) ──
    function RoundTable({ seats, onDrop, onRemove, onRename, onRemoveSeat, editingSeatId, setEditingSeatId }) {
      const [dragOverId, setDragOverId] = useState(null);
      const SZ = 56, W = 510, H = 440;
      const CX = W/2;

      const phaseMeta = [
        { key: 'l1',     label: '📊 分析层',   color: '#3b82f6', seats: 2, icon: '发表报告' },
        { key: 'debate', label: '⚔️ 辩论层',   color: '#ef4444', seats: 2, icon: '圆桌辩论' },
        { key: 'trader', label: '💼 交易层',   color: '#6366f1', seats: 1, icon: '交易方案' },
        { key: 'risk',   label: '🛡️ 风控层',   color: '#f59e0b', seats: 3, icon: '风险评估' },
        { key: 'pm',     label: '👑 决策层',   color: '#a855f7', seats: 1, icon: '最终决策' },
      ];

      // Build seat index
      const seatMap = {};
      seats.forEach(s => { const p = s.phase || 'other'; if (!seatMap[p]) seatMap[p] = []; seatMap[p].push(s); });

      return (
        <div className="relative" style={{width: W+'px', height: H+'px'}}>
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-full">
            <defs>
              <linearGradient id="flow2" x1="50%" y1="0" x2="50%" y2="100%">
                <stop offset="0%" stopColor="#8B6914" stopOpacity="0.05"/><stop offset="100%" stopColor="#8B6914" stopOpacity="0.15"/>
              </linearGradient>
            </defs>
            <rect x="0" y="0" width={W} height={H} fill="url(#flow2)" rx="10"/>

            {/* Vertical flow line */}
            <line x1={CX} y1="30" x2={CX} y2={H-15} stroke="#8B6914" strokeWidth="2" opacity="0.12"/>
            {[0,1,2,3,4].map(i => (
              <circle key={i} cx={CX} cy={40 + i*(H-60)/4} r="4" fill="#8B6914" opacity="0.18"/>
            ))}

            {/* Stage labels */}
            {phaseMeta.map((pm, i) => {
              const y = 32 + i*(H-55)/4;
              return (
                <g key={pm.key}>
                  <text x="18" y={y+14} textAnchor="start" fill={pm.color} fontSize="11" fontWeight="bold" opacity="0.55">{pm.label}</text>
                  <text x="18" y={y+28} textAnchor="start" fill={pm.color} fontSize="9" opacity="0.3">{pm.icon}</text>
                </g>
              );
            })}
          </svg>

          {/* Positioned seats */}
          {phaseMeta.map((pm, pi) => {
            const group = seatMap[pm.key] || [];
            const y = 14 + pi*(H-55)/4;
            let seatPositions;
            if (pm.seats === 1) seatPositions = [{x: CX - SZ/2}];
            else if (pm.seats === 2) seatPositions = [{x: CX - SZ - 10}, {x: CX + 10}];
            else seatPositions = [{x: CX - SZ*1.5 - 16}, {x: CX - SZ/2}, {x: CX + SZ/2 + 16}];

            return group.map((seat, si) => {
              const sx = seatPositions[si]?.x ?? CX - SZ/2;
              return (
                <div key={seat.id} className="absolute" style={{left: sx, top: y, width: SZ, height: SZ}}
                  onDragOver={e => { e.preventDefault(); setDragOverId(seat.id); }}
                  onDragLeave={() => setDragOverId(null)}
                  onDrop={e => { e.preventDefault(); setDragOverId(null);
                    try { onDrop(seat.id, JSON.parse(e.dataTransfer.getData('text/plain'))); } catch(ex) {} }}>
                  {seat.master ? (
                    <div className={`relative w-full h-full group ${dragOverId === seat.id ? 'scale-110' : ''} transition-transform`}>
                      <div className="w-full h-full rounded-full bg-gradient-to-br from-amber-600/30 to-amber-800/30 border-2 border-amber-500/40 flex flex-col items-center justify-center cursor-pointer hover:border-amber-400/60">
                        <span className="text-base">{seat.master.avatar}</span>
                        <span className="text-[7px] text-amber-300 max-w-[38px] truncate text-center">{seat.master.name}</span>
                      </div>
                      <button onClick={(e) => { e.stopPropagation(); onRemove(seat.id); }}
                        className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-red-500/50 hover:bg-red-500 text-white text-[8px] flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity" title="移除大师">✕</button>
                    </div>
                  ) : (
                    <div className={`w-full h-full rounded-full border-2 border-dashed flex flex-col items-center justify-center transition-all ${dragOverId === seat.id ? 'seat-dragover shadow-lg' : 'seat-empty border-white/8 bg-white/[0.02] hover:border-white/20 hover:bg-white/[0.04]'}`}
                      onDoubleClick={() => seat.role === 'custom' ? setEditingSeatId(seat.id) : null}>
                      {editingSeatId === seat.id ? (
                        <input type="text" className="w-10 px-1 py-0.5 bg-white/15 rounded text-white text-[6px] text-center border border-amber-500/30"
                          defaultValue={seat.label} autoFocus
                          onBlur={e => onRename(seat.id, e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') onRename(seat.id, e.target.value); if (e.key === 'Escape') setEditingSeatId(null); }} />
                      ) : (
                        <>
                          <span className="text-sm opacity-45">{seat.icon}</span>
                          <span className="text-[9px] text-white/45 mt-0.5 text-center leading-tight">{seat.label}</span>
                        </>
                      )}
                    </div>
                  )}
                </div>
              );
            });
          })}
        </div>
      );
    }

    // ── Conversation snippet (first meaningful paragraph) ──
    function getConversationSnippet(content) {
      if (!content) return '';
      // Split by double newline to get paragraphs, pick first meaningful one
      const parts = content.split('\n\n').filter(p => p.trim().length > 20);
      const first = parts[0] || content;
      let cleaned = first
        .replace(/\*{1,3}/g, '')
        .replace(/^#{1,3}\s*/gm, '')
        .replace(/^[\d一二三四五六七八九十]+[\.\、\s)]*/gm, '')
        .replace(/好的，各位。?|大家好|我是[^。]+。/g, '')
        .trim();
      if (cleaned.length > 180) cleaned = cleaned.substring(0, 180) + '…';
      return cleaned || first.substring(0, 100).replace(/\*{1,3}/g, '');
    }

    function MessageCard({ msg, compact }) {
      const s = ROLE_STYLE[msg.role] || { bg:'bg-white/5', border:'border-white/10', badge:'bg-white/10 text-white/50', emoji:'💬', label:ROLE_LABELS[msg.role]||msg.role };
      const badge = ROLE_LABELS[msg.role];
      return (
        <div className={`rounded-lg p-3 border-l-3 ${s.bg} ${s.border}`}>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-lg">{msg.master_avatar || s.emoji}</span>
            <span className="text-xs font-bold text-white">{msg.master_name || s.label}</span>
            {badge && <span className={`text-[9px] px-1.5 py-0.5 rounded-full ${s.badge}`}>{badge}</span>}
          </div>
          <p className={`text-xs text-white/60 leading-relaxed whitespace-pre-wrap ${compact ? 'max-h-20' : 'max-h-24'} overflow-y-auto`}>
            {getConversationSnippet(msg.content)}
          </p>
        </div>
      );
    }

    // ── Phase Panel: 按后端执行阶段分组展示 ──
    function PhasePanel({ messages, chatEndRef }) {
      const phaseMeta = {
        l1:    { title: '📊 分析层 · 基本面 & 技术面', icon: '🔬', desc: '分析师独立出具报告，数据来自实时 API' },
        debate:{ title: '⚔️ 辩论层 · 多空交锋', icon: '⚔️', desc: '看多与看空分析师基于分析报告展开辩论' },
        trader:{ title: '💼 交易层 · 策略制定', icon: '📐', desc: '交易策略师综合多空观点制定交易方案' },
        risk:  { title: '🛡️ 风控层 · 风险评估', icon: '⚠️', desc: '激进/保守/中立风控师辩论交易策略风险' },
        pm:    { title: '👑 决策层 · 最终投资建议', icon: '🎯', desc: '投资组合经理综合所有意见做出最终决策' },
      };

      // Build phase groups from messages (empty = show skeleton)
      const phaseKeys = ['l1', 'debate', 'trader', 'risk', 'pm'];
      const phases = [];
      if (messages.length === 0) {
        // Empty state: show all phases as skeleton
        phaseKeys.forEach(k => phases.push({ key: k, msgs: [] }));
      } else {
        let currentPhase = null;
        messages.forEach(msg => {
          const role = msg.role;
          let phase;
          if (role === 'fundamentals' || role === 'market' || role === 'sentiment' || role === 'news') phase = 'l1';
          else if (role === 'bull' || role === 'bear') phase = 'debate';
          else if (role && role.startsWith('risk_')) phase = 'risk';
          else if (role === 'manager') phase = 'pm';
          else if (role === 'market' && msg.content && (msg.content.includes('交易方向') || msg.content.includes('做多') || msg.content.includes('止损') || (msg.master_name && msg.master_name.includes('交易')))) phase = 'trader';
          else phase = 'other';

          if (currentPhase && currentPhase.key === phase) {
            currentPhase.msgs.push(msg);
          } else {
            currentPhase = { key: phase, msgs: [msg] };
            phases.push(currentPhase);
          }
        });
      }

      return (
        <div className="glass-panel flex-1 flex flex-col overflow-hidden" style={{minHeight: '300px'}}>
          <div className="px-4 py-2.5 border-b border-white/10 flex items-center gap-2 flex-shrink-0">
            <span className="text-sm text-white/60">🏛️ 圆桌进程</span>
            <span className="text-xs text-white/20">{messages.length > 0 ? `${phases.length} 个阶段 · ${messages.length} 条发言` : '等待分析开始'}</span>
            {messages.length === 0 && <span className="text-[10px] text-amber-400/40 ml-auto">输入代码后点击"开始分析"</span>}
          </div>
          <div className="flex-1 chat-scroll p-3 space-y-6">
            {phases.map((phase, pi) => {
              const meta = phaseMeta[phase.key];
              if (!meta) return null;
              const isL1 = phase.key === 'l1';
              const isDebate = phase.key === 'debate';
              const isRisk = phase.key === 'risk';
              const isTrader = phase.key === 'trader';
              const isPM = phase.key === 'pm';
              const hasContent = phase.msgs.length > 0;

              return (
                <div key={pi} className={`${hasContent ? 'fade-in' : 'opacity-30 hover:opacity-60 transition-opacity'}`}>
                  {/* Phase header */}
                  <div className="flex items-center gap-2 mb-3">
                    <span className="text-lg">{meta.icon}</span>
                    <span className={`text-sm font-bold ${hasContent ? 'text-amber-400' : 'text-white/30'}`}>{meta.title}</span>
                    <span className={`text-[10px] ${hasContent ? 'text-white/30' : 'text-white/15'}`}>
                      {hasContent ? meta.desc : '等待中…'}
                    </span>
                  </div>

                  {/* L1: Side-by-side report cards */}
                  {isL1 && (
                    <div className="grid grid-cols-2 gap-3">
                      {hasContent ? phase.msgs.slice(0, 2).map(msg => (
                        <MessageCard key={msg.id} msg={msg} compact />
                      )) : (
                        <>
                          <div className="bg-white/[0.02] border border-dashed border-white/5 rounded-xl p-4">
                            <div className="text-center text-white/15">
                              <div className="text-2xl mb-1">📊</div>
                              <div className="text-xs">基本面分析师</div>
                              <div className="text-[10px] text-white/10 mt-1">分析财务数据</div>
                            </div>
                          </div>
                          <div className="bg-white/[0.02] border border-dashed border-white/5 rounded-xl p-4">
                            <div className="text-center text-white/15">
                              <div className="text-2xl mb-1">📈</div>
                              <div className="text-xs">技术面分析师</div>
                              <div className="text-[10px] text-white/10 mt-1">分析价格趋势</div>
                            </div>
                          </div>
                        </>
                      )}
                    </div>
                  )}

                  {/* Debate: alternating bull/bear */}
                  {isDebate && (
                    <div className="space-y-2">
                      {hasContent ? phase.msgs.map(msg => (
                        <MessageCard key={msg.id} msg={msg} compact />
                      )) : (
                        <>
                          <div className="bg-white/[0.02] border border-dashed border-red-500/10 rounded-lg p-3 flex items-center gap-2">
                            <span className="text-lg opacity-20">🐂</span>
                            <span className="text-xs text-white/10">看多分析师 · 寻找支撑上涨的理由</span>
                          </div>
                          <div className="bg-white/[0.02] border border-dashed border-green-500/10 rounded-lg p-3 flex items-center gap-2">
                            <span className="text-lg opacity-20">🐻</span>
                            <span className="text-xs text-white/10">看空分析师 · 寻找下跌的风险</span>
                          </div>
                        </>
                      )}
                    </div>
                  )}

                  {/* Trader: single card */}
                  {isTrader && (
                    <div>
                      {hasContent ? <MessageCard msg={phase.msgs[0]} /> : (
                        <div className="text-center text-white/12">
                          <div className="text-2xl mb-1">💼</div>
                          <div className="text-xs">交易策略师</div>
                          <div className="text-[10px] text-white/8 mt-1">综合多空制定交易方案</div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Risk: three-column debate */}
                  {isRisk && (
                    <div className="grid grid-cols-3 gap-2">
                      {hasContent ? phase.msgs.slice(0, 3).map(msg => (
                        <MessageCard key={msg.id} msg={msg} compact />
                      )) : (
                        <>
                          {[
                            { emoji: '🔥', label: '激进风控', color: 'border-red-500/10' },
                            { emoji: '🛡️', label: '保守风控', color: 'border-green-500/10' },
                            { emoji: '⚖️', label: '中立风控', color: 'border-purple-500/10' },
                          ].map((r, i) => (
                            <div key={i} className={`bg-white/[0.02] border border-dashed ${r.color} rounded-lg p-3`}>
                              <div className="text-center text-white/10">
                                <div className="text-lg">{r.emoji}</div>
                                <div className="text-[10px]">{r.label}</div>
                              </div>
                            </div>
                          ))}
                        </>
                      )}
                    </div>
                  )}

                  {/* PM: grand presentation */}
                  {isPM && (
                    <div>
                      {hasContent ? <MessageCard msg={phase.msgs[0]} /> : (
                        <div className="text-center text-white/10">
                          <div className="text-3xl mb-2">👑</div>
                          <div className="text-sm">投资组合经理</div>
                          <div className="text-[10px] text-white/5 mt-1">综合所有意见做出最终投资决策</div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            <div ref={chatEndRef}/>
          </div>
        </div>
      );
    }

    // ── ReportViewer ──
    function ReportViewer({ type, content, onClose }) {
      const titles = { fundamentals: '📊 基本面分析报告', technical: '📈 技术面分析报告', bull: '🐂 看多分析报告', bear: '🐻 看空分析报告', trading: '💼 交易策略报告', risk: '⚠️ 风险分析报告', decision: '🎯 最终决策' };
      const sections = parseMD(content);
      return (
        <div className="glass-panel flex-1 flex flex-col overflow-hidden" style={{minHeight: '300px'}}>
          <div className="px-4 py-2.5 border-b border-white/10 flex items-center justify-between flex-shrink-0">
            <h3 className="text-sm font-medium text-white">{titles[type] || type}</h3>
            <button onClick={onClose} className="text-white/40 hover:text-white text-lg">✕</button>
          </div>
          <div className="flex-1 overflow-y-auto p-5 space-y-4 chat-scroll" style={{maxHeight: '450px'}}>
            {sections.length > 0 ? sections.map((s, i) => (
              <div key={i} className="bg-white/5 rounded-lg p-4">
                <h4 className="text-amber-400 font-medium text-sm mb-2">{s.title}</h4>
                <div className="text-sm text-white/70 leading-relaxed whitespace-pre-wrap">{s.content}</div>
              </div>
            )) : (
              <div className="bg-white/5 rounded-lg p-4">
                <div className="text-sm text-white/70 leading-relaxed whitespace-pre-wrap">{content || '暂无内容'}</div>
              </div>
            )}
          </div>
        </div>
      );
    }

    function parseMD(text) {
      if (!text) return [];
      const sections = []; const lines = text.split('\n');
      let title = '', content = [];
      for (const line of lines) {
        if (line.match(/^#{1,3}\s/)) {
          if (title && content.length > 0) sections.push({ title, content: content.join('\n').trim() });
          title = line.replace(/^#{1,3}\s/, '').trim(); content = [];
        } else { content.push(line); }
      }
      if (title) sections.push({ title, content: content.join('\n').trim() });
      return sections;
    }

    function TheoryModal({ seats, masters, onClose, onTheoryChange, onMasterAssign }) {
      const [editingSeat, setEditingSeat] = useState(null);
      const [draftText, setDraftText] = useState('');
      const configurable = seats.filter(s => s.role !== 'custom');

      const selectSeat = (s) => {
        setEditingSeat(s);
        setDraftText(s.custom_theory || s.master?.methodology || '');
      };
      const save = () => {
        if (editingSeat) { onTheoryChange(editingSeat.id, draftText); setEditingSeat(null); }
      };

      return (
        <div className="fixed inset-0 z-50 bg-black/80 flex" id="theory-modal">
          {/* 关闭按钮 */}
          <button onClick={onClose} className="absolute top-4 right-4 text-white/40 hover:text-white text-2xl z-10">✕</button>

          {/* 左侧：分析师列表 + 编辑区 */}
          <div className="flex-1 flex flex-col p-6 overflow-hidden">
            <h2 className="text-lg font-bold text-white mb-4">👥 大师理论配置</h2>
            {editingSeat ? (
              <div className="flex-1 flex flex-col glass-panel rounded-xl p-4 overflow-hidden">
                <div className="flex items-center gap-3 mb-3">
                  <span className="text-xl">{editingSeat.icon || ROLE_ICONS[editingSeat.role] || '💺'}</span>
                  <span className="text-sm font-bold text-amber-400">{editingSeat.label}</span>
                  <span className="flex-1" />
                  <button onClick={save} className="px-4 py-1.5 bg-amber-600/40 text-amber-300 rounded-lg text-xs hover:bg-amber-600/60">保存</button>
                  <button onClick={() => setEditingSeat(null)} className="px-3 py-1.5 bg-white/10 text-white/40 rounded-lg text-xs hover:bg-white/20">取消</button>
                </div>
                <textarea value={draftText} onChange={e => setDraftText(e.target.value)}
                  placeholder={`为「${editingSeat.label}」编写投资理论/分析框架…\n填入后将以「自定义理论」块注入该角色的 Prompt，优先级高于大师方法论。`}
                  className="flex-1 w-full text-sm bg-black/30 border border-white/10 rounded-lg p-4 text-white/80 placeholder-white/20 focus:border-amber-500/30 resize-none" style={{outline:'none'}}
                  onDrop={e => { e.preventDefault(); const m = JSON.parse(e.dataTransfer.getData('text/plain')); setDraftText(m.methodology || ''); }}
                  onDragOver={e => e.preventDefault()} />
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-2 overflow-y-auto flex-1">
                {configurable.map(s => {
                  const hasCustom = !!(s.custom_theory || '').trim();
                  const preview = hasCustom ? s.custom_theory.trim().substring(0, 60) + (s.custom_theory.trim().length > 60 ? '…' : '') : '';
                  const props = {
                    onDragOver: (e) => e.preventDefault(),
                    onDrop: (e) => { e.preventDefault(); try { const m = JSON.parse(e.dataTransfer.getData('text/plain')); if (m && m.name) onMasterAssign(s.id, m); } catch {} },
                  };
                  return (
                    <button key={s.id} onClick={() => selectSeat(s)}
                      className="glass-panel-dark rounded-lg p-3 text-left hover:bg-white/10 transition-colors relative" {...props}>
                      <div className="flex items-center gap-2">
                        {s.master ? (
                          <>
                            <span className="text-lg">{s.master.avatar}</span>
                            <span className="text-sm font-bold text-amber-300 truncate flex-1">{s.master.name}</span>
                          </>
                        ) : (
                          <>
                            <span className="text-lg">{s.icon || ROLE_ICONS[s.role] || '💺'}</span>
                            <span className="text-sm text-white/80">{s.label}</span>
                          </>
                        )}
                      </div>
                      {hasCustom ? (
                        <div className="mt-1.5">
                          <div className="text-[10px] text-amber-400/70 font-medium">已注入理论</div>
                          <div className="text-[10px] text-white/35 mt-1 line-clamp-2">{preview}</div>
                        </div>
                      ) : (
                        <div className="text-[10px] text-white/20 mt-1.5">拖拽大师到此处 · 点击编辑</div>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* 右侧：大师列表 */}
          <div className="w-[340px] border-l border-white/10 flex flex-col overflow-hidden">
              <div className="p-4 border-b border-white/10">
                <h3 className="text-sm font-semibold text-white/70 flex items-center gap-2">
                  <span>🧑‍🤝‍🧑</span> 投资大师 <span className="text-white/30 ml-auto text-xs">{masters.length} 位</span>
                </h3>
              </div>
              <div className="flex-1 overflow-y-auto p-3 space-y-2">
                {masters.map(m => (
                  <div key={m.id} draggable="true"
                    className="glass-panel-dark rounded-lg p-3 hover:bg-white/10 transition-colors"
                    onDragStart={e => e.dataTransfer.setData('text/plain', JSON.stringify(m))}
                    onClick={() => editingSeat ? setDraftText(m.methodology || '') : null}>
                    <div className="flex items-center gap-2">
                      <span>{m.avatar || '🧑‍💼'}</span>
                      <span className="text-sm text-white/80 truncate flex-1">{m.name}</span>
                      <span className="text-[9px] px-1.5 py-0.5 rounded border bg-white/10 text-white/50">{STYLE_LABELS[m.style]}</span>
                    </div>
                    {m.methodology && (
                      <div className="text-[10px] text-white/35 mt-1.5 line-clamp-2">{m.methodology}</div>
                    )}
                    <div className="text-[9px] text-amber-400/40 mt-1 text-center">点击注入 · 拖拽到圆桌</div>
                  </div>
                ))}
              </div>
            </div>
        </div>
      );
    }

    // ── P0: RulesPanel ──
    function RulesPanel({ rules, signal }) {
      const actionColors = {
        stop_loss: 'text-red-400', take_profit: 'text-green-400', sell_pct: 'text-amber-400',
        sell_all: 'text-red-400', buy_add: 'text-green-400', circuit_break: 'text-red-300 font-bold',
        alert_only: 'text-slate-400', rating_reeval: 'text-purple-400', hold: 'text-slate-400'
      };
      const actionLabels = {
        stop_loss: '止损', take_profit: '止盈', sell_pct: '减仓', sell_all: '清仓',
        buy_add: '加仓', circuit_break: '熔断清仓', alert_only: '预警', rating_reeval: '复评', hold: '持有'
      };
      const signalLabels = { buy: '买入', overweight: '增持', hold: '持有', underweight: '减持', sell: '卖出' };
      const signalColors = { buy: 'text-green-400', overweight: 'text-emerald-400', hold: 'text-slate-400', underweight: 'text-amber-400', sell: 'text-red-400' };

      const sorted = [...rules].sort((a, b) => (b.priority||0) - (a.priority||0));

      return (
        <div className="glass-panel overflow-hidden mt-3">
          <div className="px-4 py-3 border-b border-white/10 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">交易规则</span>
              {signal && <span className={`text-xs px-2 py-0.5 rounded-full bg-white/5 ${signalColors[signal]}`}>{signalLabels[signal] || signal}</span>}
            </div>
            <button onClick={() => {
              const text = sorted.map(r => `[${(actionLabels[r.action] || r.action).toUpperCase()}] P${r.priority}: ${r.condition_str}`).join('\n');
              navigator.clipboard.writeText(text);
            }} className="px-2.5 py-1 text-[11px] bg-white/10 hover:bg-white/15 rounded-lg transition">复制规则</button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead><tr className="text-slate-400 bg-white/[0.02]">
                <th className="text-left px-4 py-2 font-medium w-16">优先级</th>
                <th className="text-left px-4 py-2 font-medium">触发条件</th>
                <th className="text-left px-4 py-2 font-medium w-20">动作</th>
                <th className="text-left px-4 py-2 font-medium w-16">比例</th>
              </tr></thead>
              <tbody>
                {sorted.map((r, i) => (
                  <tr key={i} className="border-t border-white/5 hover:bg-white/[0.02]">
                    <td className="px-4 py-2.5 font-mono text-slate-400">{r.priority || 50}</td>
                    <td className="px-4 py-2.5 font-mono text-slate-300 max-w-xs truncate" title={r.condition_str}>{r.condition_str || '(无)'}</td>
                    <td className={`px-4 py-2.5 font-medium ${actionColors[r.action] || 'text-slate-300'}`}>{actionLabels[r.action] || r.action}</td>
                    <td className="px-4 py-2.5 font-mono text-slate-400">{r.pct > 0 ? `${(r.pct*100).toFixed(0)}%` : '-'}</td>
                  </tr>
                ))}
                {sorted.length === 0 && (
                  <tr><td colSpan="4" className="px-4 py-6 text-center text-slate-500">PM 未生成结构化规则（可能使用了散文格式，规则将从决策文本中解析）</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      );
    }

    ReactDOM.createRoot(document.getElementById('root')).render(<App />);
  