import { FormEvent, useEffect, useState } from "react";
import { api, money, pct } from "../api";
import StockChart from "../components/StockChart";
import { Empty, PageHeader, ZoneBadge } from "../components/Ui";
import { Account, Candidate, Zone } from "../types";

type StockDetail = {
  symbol: string;
  profile?: { name?: string; industry?: string };
  snapshot?: { close?: number; pe_ttm?: number };
  bars: Array<{
    date: string; open: number; high: number; low: number; close: number; volume: number;
  }>;
};

export default function Candidates() {
  const [rows, setRows] = useState<Candidate[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountId, setAccountId] = useState("");
  const [zone, setZone] = useState("");
  const [minScore, setMinScore] = useState(0);
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [stock, setStock] = useState<StockDetail | null>(null);
  const [error, setError] = useState("");

  const load = () => api<Candidate[]>(
    `/candidates?min_score=${minScore}${zone ? `&zone=${zone}` : ""}${accountId ? `&account_id=${accountId}` : ""}`
  ).then(setRows).catch((reason) => setError(reason.message));

  useEffect(() => { void api<Account[]>("/accounts").then(setAccounts); }, []);
  useEffect(() => { void load(); }, [zone, minScore, accountId]);

  const open = async (candidate: Candidate) => {
    setSelected(candidate);
    setStock(null);
    setStock(await api<StockDetail>(`/stocks/${candidate.symbol}`));
  };
  const setStatus = async (id: number, status: string) => {
    await api(`/candidates/${id}/status`, {
      method: "PUT", body: JSON.stringify({ status })
    });
    void load();
  };

  return <>
    <PageHeader
      title="四维候选追踪"
      subtitle="策略共振 · 买入区间 · 止损 · 信号后表现持续留痕"
      actions={<div className="filters">
        <select value={accountId} onChange={(event) => setAccountId(event.target.value)}>
          <option value="">不计算仓位</option>
          {accounts.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}
        </select>
        <select value={zone} onChange={(event) => setZone(event.target.value)}>
          <option value="">全部位置</option><option value="LEFT">左侧</option>
          <option value="MIDDLE">中部</option><option value="RIGHT">右侧</option>
          <option value="VETO">否决</option>
        </select>
        <select value={minScore} onChange={(event) => setMinScore(+event.target.value)}>
          <option value="0">全部分数</option><option value="5">5分以上</option>
          <option value="7">7分以上</option>
        </select>
      </div>}
    />
    {error && <div className="error-box">{error}</div>}
    <section className="table-panel">
      {rows.length === 0 ? <Empty>当前筛选条件没有候选，日跑完成后会自动出现在这里。</Empty> :
        <div className="table-scroll"><table className="candidate-table">
          <thead><tr><th>标的</th><th>赛道</th><th>现价 / PE</th><th>回撤</th><th>反弹</th>
            <th>量比</th><th>总分</th><th>位置</th><th>买入区间</th><th>止损</th>
            {accountId && <th>建议股数</th>}<th>状态</th><th>逻辑</th></tr></thead>
          <tbody>{rows.map((row) => <tr key={row.id} onClick={() => open(row)}>
            <td><b>{row.name || row.symbol}</b><small>{row.symbol} · {row.strategies.length}策略</small></td>
            <td>{row.industry || "—"}</td>
            <td><b>{money(row.real_close)}</b><small>PE {money(row.pe_ttm)}</small></td>
            <td><span className="metric negative">-{pct(row.drawdown_60)}</span></td>
            <td><span className="metric positive">+{pct(row.rebound_60)}</span></td>
            <td>{row.volume_ratio?.toFixed(2)}</td>
            <td><span className={`score score-${row.total_score}`}>{row.total_score}</span></td>
            <td><ZoneBadge zone={row.current_zone || row.zone} /></td>
            <td>{money(row.current_entry_low || row.entry_low)} – {money(row.current_entry_high || row.entry_high)}</td>
            <td className="negative">{money(row.current_stop_price || row.stop_price)}</td>
            {accountId && <td><b>{row.suggested_quantity || 0}</b><small>100股整数倍</small></td>}
            <td><select value={row.lifecycle_status} onClick={(event) => event.stopPropagation()}
              onChange={(event) => { event.stopPropagation(); void setStatus(row.id, event.target.value); }}>
              <option>NEW</option><option>WATCHING</option><option>PLANNED</option>
              <option>BOUGHT</option><option>DROPPED</option><option>EXPIRED</option>
            </select></td>
            <td className="rationale">{row.veto_reason || row.rationale}</td>
          </tr>)}</tbody>
        </table></div>}
    </section>
    {selected && <CandidateDrawer candidate={selected} stock={stock}
      onClose={() => setSelected(null)} onSaved={() => { setSelected(null); void load(); }} />}
  </>;
}

function CandidateDrawer({
  candidate, stock, onClose, onSaved
}: {
  candidate: Candidate;
  stock: StockDetail | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const [entryLow, setEntryLow] = useState(candidate.current_entry_low || candidate.entry_low || 0);
  const [entryHigh, setEntryHigh] = useState(candidate.current_entry_high || candidate.entry_high || 0);
  const [stop, setStop] = useState(candidate.current_stop_price || candidate.stop_price || 0);
  const [zone, setZone] = useState<Zone>(candidate.current_zone || candidate.zone);
  const [reason, setReason] = useState("");

  const save = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await api(`/plans/${candidate.plan_id}`, {
        method: "PUT",
        body: JSON.stringify({
          entry_low: entryLow, entry_high: entryHigh, stop_price: stop,
          zone, note: "", reason
        })
      });
      onSaved();
    } catch (cause) {
      setError((cause as Error).message);
    }
  };

  return <div className="drawer-backdrop" onMouseDown={onClose}>
    <aside className="drawer" onMouseDown={(event) => event.stopPropagation()}>
      <button className="drawer-close" onClick={onClose}>×</button>
      <div className="drawer-head"><div><span className="eyebrow">{candidate.symbol}</span>
        <h2>{candidate.name || candidate.symbol}</h2>
        <p>{candidate.industry || "未分类"} · {candidate.strategies.join(" / ")}</p></div>
        <span className={`score score-${candidate.total_score}`}>{candidate.total_score}</span></div>
      <div className="drawer-metrics"><div><span>真实现价</span><b>{money(candidate.real_close)}</b></div>
        <div><span>买入区</span><b>{money(entryLow)} – {money(entryHigh)}</b></div>
        <div><span>止损价</span><b className="negative">{money(stop)}</b></div>
        <div><span>位置</span><ZoneBadge zone={zone} /></div></div>
      {stock ? <StockChart bars={stock.bars} entryLow={entryLow} entryHigh={entryHigh} stop={stop} /> :
        <div className="skeleton">正在加载 K 线…</div>}
      <div className="logic-box"><b>K 线逻辑</b><p>{candidate.rationale}</p></div>
      {!editing ? <button className="secondary" onClick={() => setEditing(true)}>修订交易计划</button> :
        <form className="plan-form" onSubmit={save}><div className="form-row">
          <label>买入下沿<input type="number" step="0.01" value={entryLow} onChange={(event) => setEntryLow(+event.target.value)} /></label>
          <label>买入上沿<input type="number" step="0.01" value={entryHigh} onChange={(event) => setEntryHigh(+event.target.value)} /></label>
          <label>止损价<input type="number" step="0.01" value={stop} onChange={(event) => setStop(+event.target.value)} /></label>
          <label>位置<select value={zone} onChange={(event) => setZone(event.target.value as Zone)}>
            <option value="LEFT">左侧</option><option value="MIDDLE">中部</option>
            <option value="RIGHT">右侧</option><option value="VETO">否决</option>
          </select></label></div>
          <label>修订原因<textarea value={reason} onChange={(event) => setReason(event.target.value)} required /></label>
          {error && <div className="error-box">{error}</div>}
          <button className="primary">保存修订并留痕</button>
        </form>}
    </aside>
  </div>;
}
