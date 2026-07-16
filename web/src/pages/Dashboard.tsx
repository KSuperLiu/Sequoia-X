import { useEffect, useState } from "react";
import { api, money, pct } from "../api";
import { Kpi, PageHeader } from "../components/Ui";
import { DashboardData } from "../types";

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api<DashboardData>("/dashboard").then(setData).catch((e) => setError(e.message)); }, []);
  if (error) return <div className="error-box">{error}</div>;
  if (!data) return <div className="skeleton">正在汇总最新决策…</div>;
  const summary = data.summary;
  const zones = summary?.zone_counts || { LEFT: 0, MIDDLE: 0, RIGHT: 0, VETO: 0 };
  const total = Math.max(summary?.candidate_count || 0, 1);
  return <>
    <PageHeader title="核心决策总览" subtitle={data.run ? `${data.run.trade_date} 收盘数据 · ${data.run.status}` : "尚未生成追踪日报"} actions={<span className={`freshness ${data.data_stale ? "stale" : "fresh"}`}>{data.data_stale ? "数据待更新" : "数据已校验"}</span>} />
    {data.run?.message && data.data_stale && <div className="warning-banner">⚠ {data.run.message}，可查看候选但系统已关闭可执行仓位建议。</div>}
    <section className="kpi-grid">
      <Kpi label="覆盖标的" value={summary?.candidate_count || 0} note="今日策略候选并集" />
      <Kpi label="左侧机会" value={zones.LEFT} tone="green" note="标准仓位观察" />
      <Kpi label="中部机会" value={zones.MIDDLE} tone="orange" note="仅允许小仓试错" />
      <Kpi label="右侧 / 否决" value={zones.RIGHT + zones.VETO} tone="red" note="不追高、不执行" />
    </section>
    <section className="panel decision-panel"><div className="panel-title"><div><span className="pin">◆</span><h2>今日判断</h2></div><span className="date-chip">{data.run?.trade_date || "等待日跑"}</span></div><div className="decision-line"><span>总体策略</span><strong className={summary?.eligible_count ? "positive" : "negative"}>{summary?.action || "等待数据"}</strong></div><div className="decision-line"><span>默认风控</span><b>单笔风险 ≤ 1% · 总仓位 ≤ 60% · 同时持仓 ≤ 5 只</b></div><div className="distribution"><div className="bar"><i className="left" style={{ width: `${zones.LEFT / total * 100}%` }} /><i className="middle" style={{ width: `${zones.MIDDLE / total * 100}%` }} /><i className="right" style={{ width: `${(zones.RIGHT + zones.VETO) / total * 100}%` }} /></div><div className="legend"><span><i className="left" />左侧 {zones.LEFT}</span><span><i className="middle" />中部 {zones.MIDDLE}</span><span><i className="right" />右侧/否决 {zones.RIGHT + zones.VETO}</span></div></div></section>
    <section className="panel"><div className="panel-title"><div><h2>组合状态</h2><small>模拟组合与人工实盘账本统一估值</small></div></div>{data.accounts.length === 0 ? <div className="empty"><div>＋</div><p>还没有账户，请前往“组合交易”创建模拟或实盘账本。</p></div> : <div className="account-cards">{data.accounts.map((account) => <article key={account.id} className="account-card"><div><span className="account-type">{account.account_type === "PAPER" ? "模拟" : "实盘账本"}</span><h3>{account.name}</h3></div><dl><div><dt>账户权益</dt><dd>¥ {money(account.portfolio.equity)}</dd></div><div><dt>总仓位</dt><dd>{pct(account.portfolio.total_weight)}</dd></div><div><dt>浮动盈亏</dt><dd className={account.portfolio.unrealized_pnl >= 0 ? "positive" : "negative"}>¥ {money(account.portfolio.unrealized_pnl)}</dd></div><div><dt>持仓</dt><dd>{account.portfolio.positions.length} 只</dd></div></dl></article>)}</div>}</section>
  </>;
}
