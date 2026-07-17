export type Zone = "LEFT" | "MIDDLE" | "RIGHT" | "VETO";

export interface DashboardData {
  run: null | { id: number; trade_date: string; status: string; data_fresh: number; message: string };
  summary: null | {
    candidate_count: number;
    eligible_count: number;
    action: string;
    zone_counts: Record<Zone, number>;
  };
  data_stale: boolean;
  accounts: Array<Account>;
  top_candidates: Array<Pick<Candidate, "id" | "symbol" | "name" | "industry" | "total_score" | "confidence" | "consensus_count" | "real_close"> & { current_zone: Zone; plan_status: string }>;
  risk_alerts: Array<{ level: string; message: string; to: string }>;
  recent_activity: Array<{ actor: string; action: string; entity_type?: string; entity_id?: string; created_at: string }>;
  latest_job?: JobRun | null;
  ready_plan_count: number;
}

export interface Candidate {
  id: number;
  symbol: string;
  name?: string;
  industry?: string;
  trade_date: string;
  strategies: string[];
  consensus_count: number;
  confidence: string;
  drawdown_60: number;
  rebound_60: number;
  volume_ratio: number;
  total_score: number;
  real_close?: number;
  pe_ttm?: number;
  entry_low?: number;
  entry_high?: number;
  stop_price?: number;
  current_entry_low?: number;
  current_entry_high?: number;
  current_stop_price?: number;
  current_zone: Zone;
  zone: Zone;
  veto_reason?: string;
  rationale: string;
  lifecycle_status: string;
  plan_id: number;
  plan_status: string;
  suggested_quantity?: number;
  score_drawdown?: number;
  score_rebound?: number;
  score_ma?: number;
  score_volume?: number;
  return_1d?: number;
  return_3d?: number;
  return_5d?: number;
  return_10d?: number;
  return_20d?: number;
  mfe?: number;
  mae?: number;
  hit_entry?: number;
  hit_stop?: number;
  data_fresh?: number;
  account_id?: number;
}

export interface Position {
  symbol: string;
  quantity: number;
  average_cost: number;
  last_price: number;
  market_value: number;
  unrealized_pnl: number;
  return_pct: number;
  quote_date?: string;
  stop_price?: number;
  stop_distance?: number;
  risk_amount?: number;
}

export interface Portfolio {
  account_id: number;
  cash: number;
  market_value: number;
  equity: number;
  total_weight: number;
  unrealized_pnl: number;
  realized_pnl: number;
  positions: Position[];
}

export interface Account {
  id: number;
  name: string;
  account_type: "PAPER" | "REAL_LEDGER";
  initial_cash: number;
  portfolio: Portfolio;
}

export interface Paged<T> { items: T[]; total: number; page: number; page_size: number; facets?: Record<string, unknown> }

export interface WatchlistItem {
  id: number; symbol: string; name?: string; industry?: string; group_name: string; note: string;
  target_price?: number; watch_price?: number; close?: number; quote_date?: string; alert?: "TARGET" | "WATCH";
}

export interface TradePlan {
  id: number; candidate_id: number; symbol: string; name?: string; industry?: string; trade_date: string;
  total_score: number; confidence: string; consensus_count: number; real_close?: number; lifecycle_status: string;
  original_entry_low?: number; original_entry_high?: number; original_stop_price?: number; original_zone: Zone;
  current_entry_low?: number; current_entry_high?: number; current_stop_price?: number; current_zone: Zone;
  suggested_quantity: number; status: string; account_id?: number; account_name?: string; note?: string; data_fresh: number;
  revisions?: Array<{ id: number; entry_low: number; entry_high: number; stop_price: number; zone: Zone; reason: string; actor: string; created_at: string }>;
}

export interface JobRun {
  id: number; job_type: string; source: string; status: string; requested_by: string; requested_at: string;
  started_at?: string; finished_at?: string; current_stage?: string; progress_current: number; progress_total: number;
  message?: string; exit_code?: number; cancel_requested?: number; cancel_requested_at?: string; cancel_requested_by?: string;
  logs?: Array<{ id: number; level: string; message: string; created_at: string }>;
}
