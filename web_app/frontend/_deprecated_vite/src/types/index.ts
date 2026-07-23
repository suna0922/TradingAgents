export interface Master {
  id: string;
  name: string;
  title: string;
  avatar_url: string;
  style: 'value' | 'growth' | 'momentum' | 'quant' | 'macro' | 'contrarian';
  methodology: string;
  best_for: string[];
}

export interface Seat {
  id: string;
  role: string;
  label: string;
  description: string;
  master: Master | null;
  custom_theory?: string;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: string;
  master_name: string;
  master_avatar: string;
  content: string;
  timestamp: string;
  is_complete: boolean;
}

export interface StockBasicInfo {
  ticker: string;
  name: string;
  market: string;
  industry: string;
  area: string;
  list_date: string;
}

export interface OHLCVPoint {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface TechnicalIndicators {
  ticker: string;
  stock_name: string;
  analysis_date: string;
  latest_price: number;
  change_pct: number;
  sma_5: number;
  sma_10: number;
  sma_20: number;
  sma_60: number;
  ema_12: number;
  ema_26: number;
  macd: number;
  macd_signal: number;
  macd_hist: number;
  rsi_6: number;
  rsi_14: number;
  rsi_24: number;
  boll_upper: number;
  boll_mid: number;
  boll_lower: number;
  atr_14: number;
  kdj_k: number;
  kdj_d: number;
  kdj_j: number;
  volume_ratio: number;
  turn_over: number;
  ohlcv_history: OHLCVPoint[];
}

export interface FundamentalMetric {
  name: string;
  value: number;
  unit: string;
  yoy: number | null;
  qoq: number | null;
}

export interface FundamentalSection {
  title: string;
  metrics: FundamentalMetric[];
}

export interface FundamentalsData {
  ticker: string;
  stock_name: string;
  report_date: string;
  sections: FundamentalSection[];
  raw_report_md: string;
}

export interface SessionInfo {
  session_id: string;
  ticker: string;
  stock_name: string;
  status: 'created' | 'fetching_data' | 'analyzing' | 'debating' | 'deciding' | 'completed' | 'error';
  created_at: string;
  completed_at: string | null;
  signal: 'buy' | 'overweight' | 'hold' | 'underweight' | 'sell' | null;
  seats: Seat[];
}

export interface ReportData {
  session_id: string;
  type: string;
  content: string;
}
