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
