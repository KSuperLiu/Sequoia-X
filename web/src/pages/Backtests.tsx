import { FormEvent, useEffect, useState } from "react";
import { api, pct } from "../api";
import { Empty, PageHeader } from "../components/Ui";

type Backtest = {
  id: number;
  strategy_name: string;
  start_date: string;
  end_date: string;
  status: string;
  metrics?: {
    trade_count: number;
    total_return: number;
    annualized_return: number;
    max_drawdown: number;
    win_rate: number;
    sharpe: number;
    warning: string;
  };
};

export default function Backtests() {
  const [rows, setRows] = useState<Backtest[]>([]);
  const [show, setShow] = useState(false);
  const load = () => api<Backtest[]>("/backtests").then(setRows);
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);
  return <>
    <PageHeader title="基础策略回测" subtitle="次日开盘成交 · 风险仓位约束 · 止损与20日退出 · 明示幸存者偏差" actions={<button className="primary" onClick={() => setShow(!show)}>＋ 新建回测</button>} />
    {show && <BacktestForm onSaved={() => { setShow(false); void load(); }} />}
    {rows.length === 0 ? <Empty>还没有回测记录。基础回测会在后台运行，时间取决于日期范围。</Empty> : <div className="backtest-list">{rows.map((row) => <article className="panel" key={row.id}>
      <div className="panel-title"><div><span className="eyebrow">{row.strategy_name}</span><h2>{row.start_date} → {row.end_date}</h2></div><span className={`status ${row.status.toLowerCase()}`}>{row.status}</span></div>
      {row.metrics ? <><div className="metric-row"><div><span>总收益</span><b className={row.metrics.total_return >= 0 ? "positive" : "negative"}>{pct(row.metrics.total_return)}</b></div><div><span>年化</span><b>{pct(row.metrics.annualized_return)}</b></div><div><span>最大回撤</span><b className="negative">{pct(row.metrics.max_drawdown)}</b></div><div><span>胜率</span><b>{pct(row.metrics.win_rate)}</b></div><div><span>夏普</span><b>{row.metrics.sharpe?.toFixed(2)}</b></div><div><span>交易数</span><b>{row.metrics.trade_count}</b></div></div><div className="warning-banner">⚠ {row.metrics.warning}</div></> : <div className="skeleton">{row.status === "FAILED" ? "回测失败，请检查任务详情" : "后台计算中，请勿用回测结果替代独立投资判断…"}</div>}
    </article>)}</div>}
  </>;
}

function BacktestForm({ onSaved }: { onSaved: () => void }) {
  const [form, setForm] = useState({
    strategy_name: "TurtleTradeStrategy",
    start_date: "2025-01-01",
    end_date: new Date().toISOString().slice(0, 10),
    initial_cash: 1000000,
    commission_rate: 0.0003,
    minimum_commission: 5,
    stamp_duty_rate: 0.0005,
    transfer_fee_rate: 0.00001
  });
  const [error, setError] = useState("");
  const set = (key: string, value: string | number) => setForm({ ...form, [key]: value });
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await api("/backtests", { method: "POST", body: JSON.stringify(form) });
      onSaved();
    } catch (reason) {
      setError((reason as Error).message);
    }
  };
  return <form className="panel inline-form" onSubmit={submit}>
    <label>策略<select value={form.strategy_name} onChange={(e) => set("strategy_name", e.target.value)}><option>MaVolumeStrategy</option><option>TurtleTradeStrategy</option><option>HighTightFlagStrategy</option><option>LimitUpShakeoutStrategy</option><option>UptrendLimitDownStrategy</option><option>RpsBreakoutStrategy</option></select></label>
    <label>开始<input type="date" value={form.start_date} onChange={(e) => set("start_date", e.target.value)} /></label>
    <label>结束<input type="date" value={form.end_date} onChange={(e) => set("end_date", e.target.value)} /></label>
    <label>初始资金<input type="number" value={form.initial_cash} onChange={(e) => set("initial_cash", +e.target.value)} /></label>
    <button className="primary">提交后台回测</button>{error && <div className="error-box">{error}</div>}
  </form>;
}
