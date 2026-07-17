import { useEffect, useState } from "react";
import { ArrowRight, BriefcaseBusiness, CircleAlert, Clock3, Play, RefreshCw, ShieldAlert, Star } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, money, pct } from "../api";
import { Kpi, PageHeader, StatusBadge, ZoneBadge } from "../components/Ui";
import { useToast } from "../components/Toast";
import { DashboardData } from "../types";

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const toast = useToast();
  const load = () => api<DashboardData>("/dashboard").then(setData).catch((e) => setError(e.message));
  useEffect(() => { void load(); }, []);
  const trigger = async () => { try { await api("/runs/trigger", { method: "POST" }); toast.show("数据更新任务已加入队列"); void load(); } catch (e) { toast.show((e as Error).message, "error"); } };
  if (error) return <div className="error-box">{error}<button className="text-button" onClick={load}>重试</button></div>;
  if (!data) return <div className="skeleton">正在汇总最新决策…</div>;
  const summary = data.summary;
  const zones = summary?.zone_counts || { LEFT: 0, MIDDLE: 0, RIGHT: 0, VETO: 0 };
  const total = Math.max(summary?.candidate_count || 0, 1);
  return <>
    <PageHeader title="核心决策工作台" subtitle={data.run ? `${data.run.trade_date} 收盘数据 · 决策、计划与账户风险一屏掌握` : "尚未生成追踪日报"} actions={<><span className={`freshness ${data.data_stale ? "stale" : "fresh"}`}>{data.data_stale ? "数据待更新" : "数据已校验"}</span><button className="primary icon-button" onClick={trigger} disabled={data.latest_job?.status === "RUNNING" || data.latest_job?.status === "PENDING"}><RefreshCw size={15} />{data.latest_job?.status === "RUNNING" ? "更新中" : "更新数据"}</button></>} />
    {data.data_stale && <div className="warning-banner"><ShieldAlert size={16} /> 行情数据未通过新鲜度校验，可查看历史结果，但不可执行仓位建议。<button onClick={() => navigate("/tasks")}>查看任务</button></div>}
    <section className="kpi-grid">
      <Kpi label="覆盖标的" value={summary?.candidate_count || 0} note="今日策略候选集合" onClick={() => navigate("/candidates")} />
      <Kpi label="左侧机会" value={zones.LEFT} tone="green" note="标准仓位观察" onClick={() => navigate("/candidates?zone=LEFT")} />
      <Kpi label="中部机会" value={zones.MIDDLE} tone="orange" note="仅允许小仓试错" onClick={() => navigate("/candidates?zone=MIDDLE")} />
      <Kpi label="右侧 / 否决" value={zones.RIGHT + zones.VETO} tone="red" note="不追高、不执行" onClick={() => navigate("/candidates?zone=RIGHT%2CVETO")} />
    </section>
    <section className="dashboard-grid">
      <article className="panel decision-panel"><div className="panel-title"><div><Star size={17} /><h2>今日判断</h2></div><span className="date-chip">{data.run?.trade_date || "等待日跑"}</span></div><div className="decision-line"><span>总体策略</span><strong className={summary?.eligible_count ? "positive" : "negative"}>{summary?.action || "等待数据"}</strong></div><div className="decision-line"><span>风险约束</span><b>单笔 1% · 总仓位 60% · 最多 5 只</b></div><div className="distribution"><div className="bar"><i className="left" style={{ width: `${zones.LEFT / total * 100}%` }} /><i className="middle" style={{ width: `${zones.MIDDLE / total * 100}%` }} /><i className="right" style={{ width: `${(zones.RIGHT + zones.VETO) / total * 100}%` }} /></div><div className="legend"><span><i className="left" />左侧 {zones.LEFT}</span><span><i className="middle" />中部 {zones.MIDDLE}</span><span><i className="right" />右侧/否决 {zones.RIGHT + zones.VETO}</span></div></div></article>
      <article className="panel"><div className="panel-title"><div><CircleAlert size={17} /><h2>风险提醒</h2></div><button className="text-button" onClick={() => navigate("/portfolio")}>组合详情 <ArrowRight size={14} /></button></div>{data.risk_alerts.length === 0 ? <div className="safe-state">当前没有需要立即处理的风险</div> : <div className="alert-list">{data.risk_alerts.slice(0, 6).map((item, index) => <button key={`${item.message}-${index}`} onClick={() => navigate(item.to)}><span className={`risk-dot ${item.level.toLowerCase()}`} /><span>{item.message}</span><ArrowRight size={14} /></button>)}</div>}</article>
    </section>
    <section className="panel quick-actions"><div><h2>快捷操作</h2><p>常用操作直接进入完整流程</p></div><button onClick={() => navigate("/candidates")}><Star size={18} /><span><b>筛选机会</b><small>查看高分和策略共振</small></span></button><button onClick={() => navigate("/plans?status=READY")}><Play size={18} /><span><b>执行计划</b><small>处理待执行买入计划</small></span></button><button onClick={() => navigate("/portfolio")}><BriefcaseBusiness size={18} /><span><b>录入成交</b><small>模拟或实盘账本</small></span></button><button onClick={() => navigate("/tasks")}><Clock3 size={18} /><span><b>任务中心</b><small>进度、日志与重试</small></span></button></section>
    <section className="table-panel"><div className="panel-title"><div><h2>优先候选</h2><small>按位置、总分和策略共振排序</small></div><button className="text-button" onClick={() => navigate("/candidates")}>查看全部 <ArrowRight size={14} /></button></div><div className="table-scroll"><table><thead><tr><th>标的</th><th>行业</th><th>现价</th><th>总分</th><th>共振</th><th>置信度</th><th>位置</th><th>计划</th></tr></thead><tbody>{data.top_candidates.map((row) => <tr key={row.id} className="click-row" onClick={() => navigate(`/stocks/${row.symbol}`)}><td><b>{row.name || row.symbol}</b><small>{row.symbol}</small></td><td>{row.industry || "—"}</td><td>{money(row.real_close)}</td><td><span className={`score score-${row.total_score}`}>{row.total_score}</span></td><td>{row.consensus_count} 个策略</td><td>{row.confidence}</td><td><ZoneBadge zone={row.current_zone} /></td><td><StatusBadge status={row.plan_status} /></td></tr>)}</tbody></table></div></section>
    <section className="dashboard-grid"><article className="panel"><div className="panel-title"><div><BriefcaseBusiness size={17} /><h2>组合状态</h2></div></div>{data.accounts.length === 0 ? <button className="empty-action" onClick={() => navigate("/portfolio")}>创建第一个模拟或实盘账本</button> : <div className="account-cards">{data.accounts.map((account) => <button key={account.id} className="account-card" onClick={() => navigate(`/portfolio?account=${account.id}`)}><span className="account-type">{account.account_type === "PAPER" ? "模拟" : "实盘账本"}</span><h3>{account.name}</h3><dl><div><dt>权益</dt><dd>¥ {money(account.portfolio.equity)}</dd></div><div><dt>仓位</dt><dd>{pct(account.portfolio.total_weight)}</dd></div><div><dt>浮盈</dt><dd className={account.portfolio.unrealized_pnl >= 0 ? "positive" : "negative"}>¥ {money(account.portfolio.unrealized_pnl)}</dd></div><div><dt>持仓</dt><dd>{account.portfolio.positions.length} 只</dd></div></dl></button>)}</div>}</article><article className="panel"><div className="panel-title"><div><Clock3 size={17} /><h2>最近活动</h2></div>{data.latest_job && <StatusBadge status={data.latest_job.status} />}</div><div className="activity-list">{data.recent_activity.slice(0, 7).map((item, index) => <div key={`${item.created_at}-${index}`}><span /><p><b>{item.action}</b><small>{item.actor} · {new Date(item.created_at).toLocaleString("zh-CN")}</small></p></div>)}</div></article></section>
  </>;
}
