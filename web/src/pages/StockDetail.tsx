import { useEffect, useState } from "react";
import { ArrowLeft, BriefcaseBusiness, ClipboardList, ExternalLink, Star } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { api, money, pct } from "../api";
import { useAuth } from "../auth";
import StockChart from "../components/StockChart";
import ValuationPanel, { ValuationData } from "../components/ValuationPanel";
import { Empty, PageHeader, StatusBadge, ZoneBadge } from "../components/Ui";
import { useToast } from "../components/Toast";
import { strategyLabel } from "../strategies";
import { Candidate } from "../types";

type Detail = {
  symbol: string; profile?: { name?: string; industry?: string; is_st?: number };
  snapshot?: { close?: number; pe_ttm?: number; pb_mrq?: number; date?: string; trade_status?: number };
  bars: Array<{ date: string; open: number; high: number; low: number; close: number; volume: number }>;
  candidates: Candidate[]; fills: Array<Record<string, string | number | null>>; watchlist?: { id: number };
  positions: Array<{ account_name: string; quantity: number; average_cost: number; last_price: number; unrealized_pnl: number; return_pct: number; stop_price?: number; stop_distance?: number }>;
  valuation?: ValuationData;
};

function xueqiuUrl(symbol: string) {
  const market = symbol.startsWith("4") || symbol.startsWith("8") || symbol.startsWith("92")
    ? "BJ"
    : symbol.startsWith("6") || symbol.startsWith("9")
      ? "SH"
      : "SZ";
  return `https://xueqiu.com/S/${market}${symbol}`;
}

export default function StockDetail() {
  const user = useAuth();
  const { symbol = "" } = useParams(); const navigate = useNavigate(); const toast = useToast();
  const [data, setData] = useState<Detail | null>(null); const [error, setError] = useState("");
  const load = () => api<Detail>(`/stocks/${symbol}`).then(setData).catch((e) => setError(e.message));
  useEffect(() => { void load(); }, [symbol]);
  if (error) return <div className="error-box">{error}<button className="text-button" onClick={load}>重试</button></div>;
  if (!data) return <div className="skeleton">正在加载股票研究档案…</div>;
  const latest = data.candidates[0];
  const addWatch = async () => { try { await api("/watchlist", { method: "POST", body: JSON.stringify({ symbol, group_name: "默认分组", note: "", target_price: null, watch_price: null }) }); toast.show("已加入自选股"); load(); } catch (e) { toast.show((e as Error).message, "error"); } };
  return <><button className="back-link" onClick={() => navigate(-1)}><ArrowLeft size={15} />返回</button><PageHeader title={<><a className="icon-button stock-external-link" href={xueqiuUrl(symbol)} target="_blank" rel="noopener noreferrer" title={`在雪球查看${data.profile?.name || symbol}`} aria-label={`在雪球查看${data.profile?.name || symbol}`}>{data.profile?.name || symbol}<ExternalLink size={18} /></a> <span className="stock-title-code">{symbol}</span></>} subtitle={`${data.profile?.industry || "行业待补充"} · 行情日 ${data.snapshot?.date || "—"} · 真实价格口径`} actions={<>{user && <button className="secondary icon-button" onClick={addWatch} disabled={Boolean(data.watchlist)}><Star size={15} />{data.watchlist ? "已在自选" : "加入自选"}</button>}{latest && <button className="secondary icon-button" onClick={() => navigate(`/plans?q=${symbol}`)}><ClipboardList size={15} />查看计划</button>}{user && <button className="primary icon-button" onClick={() => navigate(`/portfolio?symbol=${symbol}&price=${data.snapshot?.close || ""}`)}><BriefcaseBusiness size={15} />录入成交</button>}</>} />
    <section className="quote-strip"><div><span>最新价</span><b>{money(data.snapshot?.close)}</b></div><div><span>PE TTM</span><b>{money(data.snapshot?.pe_ttm)}</b></div><div><span>PB MRQ</span><b>{money(data.snapshot?.pb_mrq)}</b></div><div><span>交易状态</span><b>{data.snapshot?.trade_status === 0 ? "停牌" : data.profile?.is_st ? "ST" : "正常"}</b></div>{latest && <><div><span>四维总分</span><b>{latest.total_score}/8</b></div><div><span>当前位置</span><ZoneBadge zone={latest.current_zone || latest.zone} /></div></>}</section>
    <ValuationPanel symbol={symbol} data={data.valuation || { symbol, history: [] }} isAdmin={user?.role === "ADMIN"} onSaved={load} />
    <section className="panel"><div className="panel-title"><div><h2>价格与交易计划</h2><small>最近 120 个交易日 · MA10/20/60 · 成交量</small></div></div><StockChart bars={data.bars} entryLow={latest?.current_entry_low || latest?.entry_low || 0} entryHigh={latest?.current_entry_high || latest?.entry_high || 0} stop={latest?.current_stop_price || latest?.stop_price || 0} latestPrice={data.snapshot?.close} /></section>
    {latest && <section className="dashboard-grid"><article className="panel"><div className="panel-title"><div><h2>四维评分拆解</h2></div><span className={`score score-${latest.total_score}`}>{latest.total_score}</span></div><div className="score-breakdown"><div><span>60 日回撤</span><b>{latest.score_drawdown || 0}/3</b><small>{pct(latest.drawdown_60)}</small></div><div><span>低点反弹</span><b>{latest.score_rebound || 0}/2</b><small>{pct(latest.rebound_60)}</small></div><div><span>MA10 位置</span><b>{latest.score_ma || 0}/2</b><small>趋势位置</small></div><div><span>量价配合</span><b>{latest.score_volume || 0}/1</b><small>量比 {latest.volume_ratio?.toFixed(2)}</small></div></div><div className="logic-box"><b>K 线逻辑</b><p>{latest.rationale}</p></div></article><article className="panel"><div className="panel-title"><div><h2>信号后表现</h2></div></div><div className="performance-grid">{[["1日", latest.return_1d], ["3日", latest.return_3d], ["5日", latest.return_5d], ["10日", latest.return_10d], ["20日", latest.return_20d], ["MFE", latest.mfe], ["MAE", latest.mae]].map(([label, value]) => <div key={String(label)}><span>{label}</span><b className={typeof value === "number" ? value >= 0 ? "positive" : "negative" : ""}>{typeof value === "number" ? pct(value) : "—"}</b></div>)}</div><div className="event-tags"><span className={latest.hit_entry ? "hit" : ""}>买入区 {latest.hit_entry ? "已触及" : "未触及"}</span><span className={latest.hit_stop ? "danger" : ""}>止损 {latest.hit_stop ? "已触发" : "未触发"}</span></div></article></section>}
    <section className="dashboard-grid"><article className="table-panel"><div className="panel-title"><div><h2>候选与计划历史</h2><small>原始信号永久保留</small></div></div>{data.candidates.length === 0 ? <Empty>暂无策略信号。</Empty> : <div className="table-scroll"><table><thead><tr><th>日期</th><th>策略</th><th>分数</th><th>位置</th><th>计划</th><th>买入区 / 止损</th></tr></thead><tbody>{data.candidates.map((row) => <tr key={row.id} className="click-row" onClick={() => navigate(`/plans?q=${symbol}`)}><td>{row.trade_date}</td><td>{row.strategies.map(strategyLabel).join(" / ")}<small>{row.strategies.join(" / ")}</small></td><td>{row.total_score}</td><td><ZoneBadge zone={row.current_zone || row.zone} /></td><td><StatusBadge status={row.plan_status} /></td><td>{money(row.current_entry_low)}–{money(row.current_entry_high)} / {money(row.current_stop_price)}</td></tr>)}</tbody></table></div>}</article><article className="panel"><div className="panel-title"><div><h2>当前持仓</h2></div></div>{data.positions.length === 0 ? <Empty>所有账户均未持有该股票。</Empty> : <div className="position-cards">{data.positions.map((row) => <div key={row.account_name}><b>{row.account_name}</b><span>{row.quantity} 股 · 成本 {money(row.average_cost)}</span><strong className={row.unrealized_pnl >= 0 ? "positive" : "negative"}>¥ {money(row.unrealized_pnl)} / {pct(row.return_pct)}</strong><small>止损 {money(row.stop_price)} · 距离 {pct(row.stop_distance)}</small></div>)}</div>}</article></section>
    <section className="table-panel"><div className="panel-title"><div><h2>成交记录</h2><small>包含模拟和实盘账本，冲正记录仍保留</small></div></div>{data.fills.length === 0 ? <Empty>暂无成交记录。</Empty> : <div className="table-scroll"><table><thead><tr><th>日期</th><th>账户</th><th>方向</th><th>数量</th><th>价格</th><th>状态</th></tr></thead><tbody>{data.fills.map((row) => <tr key={String(row.id)}><td>{row.trade_date}</td><td>{row.account_name}</td><td>{row.side}</td><td>{row.quantity}</td><td>{money(Number(row.price))}</td><td>{row.reversal_id ? <span className="status failed">已冲正</span> : <span className="status succeeded">有效</span>}</td></tr>)}</tbody></table></div>}</section>
  </>;
}
