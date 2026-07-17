import { FormEvent, useDeferredValue, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Download, Edit3, FilterX, Plus, Search, Star, Trash2 } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, money, pct } from "../api";
import { Empty, Modal, PageHeader, StatusBadge, ZoneBadge } from "../components/Ui";
import { useToast } from "../components/Toast";
import { Account, Candidate, Paged, WatchlistItem } from "../types";

type StockHit = { symbol: string; name?: string; industry?: string; snapshot?: { close?: number } };

export default function Candidates() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "candidates";
  const setTab = (value: string) => setParams(value === "candidates" ? {} : { tab: value });
  return <><PageHeader title="机会中心" subtitle="策略候选与独立自选统一跟踪，所有信号、计划和后续表现持续留痕" actions={<div className="segmented"><button className={tab === "candidates" ? "active" : ""} onClick={() => setTab("candidates")}>策略候选</button><button className={tab === "watchlist" ? "active" : ""} onClick={() => setTab("watchlist")}><Star size={14} />自选股</button></div>} />{tab === "candidates" ? <CandidateTable /> : <Watchlist />}</>;
}

function CandidateTable() {
  const navigate = useNavigate();
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<Paged<Candidate>>({ items: [], total: 0, page: 1, page_size: 30 });
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [filters, setFilters] = useState({
    q: searchParams.get("q") || "",
    zone: searchParams.get("zone") || "",
    min_score: searchParams.get("min_score") || "0",
    lifecycle: searchParams.get("lifecycle") || "",
    plan_status: searchParams.get("plan_status") || "",
    confidence: searchParams.get("confidence") || "",
    sort: searchParams.get("sort") || "score_desc",
    account_id: searchParams.get("account_id") || "",
    page: searchParams.get("page") || "1",
  });
  const [loading, setLoading] = useState(true);
  const deferredQuery = useDeferredValue(filters.q);
  const query = useMemo(() => new URLSearchParams(Object.entries({ ...filters, q: deferredQuery }).filter(([, value]) => value !== "")).toString(), [filters, deferredQuery]);
  const load = () => { setLoading(true); api<Paged<Candidate>>(`/candidates/search?${query}`).then(setData).catch((e) => toast.show(e.message, "error")).finally(() => setLoading(false)); };
  useEffect(() => { void api<Account[]>("/accounts").then(setAccounts); }, []);
  useEffect(() => { void load(); }, [query]);
  const persistFilters = (next: typeof filters) => {
    const defaults: typeof filters = { q: "", zone: "", min_score: "0", lifecycle: "", plan_status: "", confidence: "", sort: "score_desc", account_id: "", page: "1" };
    setSearchParams(Object.fromEntries(Object.entries(next).filter(([key, value]) => value !== defaults[key as keyof typeof defaults])), { replace: true });
  };
  const update = (key: string, value: string) => {
    const next = { ...filters, [key]: value, page: key === "page" ? value : "1" };
    setFilters(next);
    persistFilters(next);
  };
  const bulk = async (status: string) => { let failed = 0; for (const id of selected) { try { await api(`/candidates/${id}/status`, { method: "PUT", body: JSON.stringify({ status }) }); } catch { failed += 1; } } toast.show(failed ? `${selected.length - failed} 条已更新，${failed} 条因状态限制跳过` : `${selected.length} 条候选已更新`, failed ? "error" : "success"); setSelected([]); load(); };
  const reset = () => {
    const next = { q: "", zone: "", min_score: "0", lifecycle: "", plan_status: "", confidence: "", sort: "score_desc", account_id: "", page: "1" };
    setFilters(next);
    setSearchParams({}, { replace: true });
  };
  return <>
    <section className="filter-panel"><div className="search-field"><Search size={15} /><input value={filters.q} onChange={(e) => update("q", e.target.value)} placeholder="代码或名称" /></div><select aria-label="位置筛选" value={filters.zone} onChange={(e) => update("zone", e.target.value)}><option value="">全部位置</option><option value="LEFT">左侧</option><option value="MIDDLE">中部</option><option value="RIGHT,VETO">右侧 / 否决</option><option value="RIGHT">仅右侧</option><option value="VETO">仅否决</option></select><select value={filters.min_score} onChange={(e) => update("min_score", e.target.value)}><option value="0">全部分数</option><option value="5">5 分以上</option><option value="6">6 分以上</option><option value="7">7 分以上</option></select><select value={filters.confidence} onChange={(e) => update("confidence", e.target.value)}><option value="">全部置信度</option><option value="HIGH">高置信</option><option value="MEDIUM">中等</option><option value="LOW">低</option></select><select value={filters.lifecycle} onChange={(e) => update("lifecycle", e.target.value)}><option value="">全部生命周期</option><option value="NEW">新信号</option><option value="WATCHING">关注中</option><option value="PLANNED">已计划</option><option value="BOUGHT">已买入</option><option value="DROPPED">已放弃</option><option value="EXPIRED">已过期</option></select><select value={filters.plan_status} onChange={(e) => update("plan_status", e.target.value)}><option value="">全部计划</option><option value="DRAFT">草稿</option><option value="READY">待执行</option><option value="EXECUTED">已执行</option><option value="CANCELLED">已取消</option><option value="EXPIRED">已过期</option></select><select value={filters.account_id} onChange={(e) => update("account_id", e.target.value)}><option value="">不计算仓位</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</select><select value={filters.sort} onChange={(e) => update("sort", e.target.value)}><option value="score_desc">分数从高到低</option><option value="score_asc">分数从低到高</option><option value="price_desc">价格从高到低</option></select><button className="secondary icon-button" onClick={reset}><FilterX size={15} />重置</button><a className="secondary icon-button" href={`/api/v1/candidates/export.csv?zone=${encodeURIComponent(filters.zone)}&min_score=${filters.min_score}`}><Download size={15} />导出</a></section>
    {selected.length > 0 && <div className="bulk-bar"><b>已选择 {selected.length} 条</b><button onClick={() => bulk("WATCHING")}>加入关注</button><button onClick={() => bulk("PLANNED")}>转为计划</button><button className="danger-text" onClick={() => bulk("DROPPED")}>放弃</button><button onClick={() => setSelected([])}>取消选择</button></div>}
    <section className="table-panel">{loading ? <div className="skeleton">正在筛选候选…</div> : data.items.length === 0 ? <Empty>当前条件没有候选，请调整筛选或完成一次数据更新。</Empty> : <div className="table-scroll"><table className="candidate-table"><thead><tr><th><input type="checkbox" checked={selected.length === data.items.length && data.items.length > 0} onChange={(e) => setSelected(e.target.checked ? data.items.map((row) => row.id) : [])} /></th><th>标的</th><th>赛道</th><th>现价 / PE</th><th>回撤</th><th>反弹</th><th>量比</th><th>总分</th><th>共振</th><th>位置</th><th>买入区间</th><th>止损</th>{filters.account_id && <th>建议股数</th>}<th>生命周期</th><th>计划</th></tr></thead><tbody>{data.items.map((row) => <tr key={row.id} onClick={() => navigate(`/stocks/${row.symbol}`)}><td onClick={(e) => e.stopPropagation()}><input type="checkbox" checked={selected.includes(row.id)} onChange={(e) => setSelected((old) => e.target.checked ? [...old, row.id] : old.filter((id) => id !== row.id))} /></td><td><b>{row.name || row.symbol}</b><small>{row.symbol}</small></td><td>{row.industry || "—"}</td><td><b>{money(row.real_close)}</b><small>PE {money(row.pe_ttm)}</small></td><td className="negative">-{pct(row.drawdown_60)}</td><td className="positive">+{pct(row.rebound_60)}</td><td>{row.volume_ratio?.toFixed(2)}</td><td><span className={`score score-${row.total_score}`}>{row.total_score}</span></td><td>{row.consensus_count}<small>{row.confidence}</small></td><td><ZoneBadge zone={row.current_zone || row.zone} /></td><td>{money(row.current_entry_low || row.entry_low)} – {money(row.current_entry_high || row.entry_high)}</td><td className="negative">{money(row.current_stop_price || row.stop_price)}</td>{filters.account_id && <td><b>{row.suggested_quantity || 0}</b></td>}<td>{row.lifecycle_status}</td><td><StatusBadge status={row.plan_status} /></td></tr>)}</tbody></table></div>}<footer className="pagination"><span>共 {data.total} 条 · 第 {data.page} 页</span><div><button disabled={data.page <= 1} onClick={() => update("page", String(data.page - 1))}><ChevronLeft size={16} /></button><button disabled={data.page * data.page_size >= data.total} onClick={() => update("page", String(data.page + 1))}><ChevronRight size={16} /></button></div></footer></section>
  </>;
}

function Watchlist() {
  const [rows, setRows] = useState<WatchlistItem[]>([]);
  const [show, setShow] = useState(false);
  const [editing, setEditing] = useState<WatchlistItem | null>(null);
  const toast = useToast();
  const navigate = useNavigate();
  const load = () => api<WatchlistItem[]>("/watchlist").then(setRows).catch((e) => toast.show(e.message, "error"));
  useEffect(() => { void load(); }, []);
  const remove = async (item: WatchlistItem) => { if (!window.confirm(`确认从自选股移除 ${item.name || item.symbol}？`)) return; await api(`/watchlist/${item.id}`, { method: "DELETE" }); toast.show("已从自选股移除"); load(); };
  return <><div className="section-actions"><div><b>独立自选股</b><span>目标价和关注价在日跑后检查，并写入飞书日报摘要</span></div><button className="primary icon-button" onClick={() => setShow(true)}><Plus size={15} />添加自选</button></div><section className="table-panel">{rows.length === 0 ? <Empty>还没有自选股，可搜索完整本地股票池添加。</Empty> : <div className="table-scroll"><table><thead><tr><th>标的</th><th>分组</th><th>最新价</th><th>目标价</th><th>关注价</th><th>状态</th><th>备注</th><th>操作</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id} className="click-row" onClick={() => navigate(`/stocks/${row.symbol}`)}><td><b>{row.name || row.symbol}</b><small>{row.symbol} · {row.industry || "—"}</small></td><td>{row.group_name}</td><td>{money(row.close)}<small>{row.quote_date || "等待日跑"}</small></td><td>{money(row.target_price)}</td><td>{money(row.watch_price)}</td><td>{row.alert === "TARGET" ? <span className="status succeeded">已达目标</span> : row.alert === "WATCH" ? <span className="status failed">跌破关注</span> : <span className="status">跟踪中</span>}</td><td>{row.note || "—"}</td><td><div className="row-actions"><button title="编辑" onClick={(e) => { e.stopPropagation(); setEditing(row); }}><Edit3 size={15} /></button><button className="danger-text" title="移除" onClick={(e) => { e.stopPropagation(); void remove(row); }}><Trash2 size={15} /></button></div></td></tr>)}</tbody></table></div>}</section>{show && <Modal title="添加自选股" onClose={() => setShow(false)}><WatchForm onSaved={() => { setShow(false); load(); }} /></Modal>}{editing && <Modal title={`编辑自选 · ${editing.name || editing.symbol}`} onClose={() => setEditing(null)}><WatchEditForm item={editing} onSaved={() => { setEditing(null); load(); }} /></Modal>}</>;
}

function WatchForm({ onSaved }: { onSaved: () => void }) {
  const [query, setQuery] = useState(""); const [hits, setHits] = useState<StockHit[]>([]); const [symbol, setSymbol] = useState("");
  const [form, setForm] = useState({ group_name: "默认分组", note: "", target_price: "", watch_price: "" }); const toast = useToast();
  useEffect(() => { if (!query.trim()) { setHits([]); return; } const timer = setTimeout(() => api<StockHit[]>(`/stocks/search?q=${encodeURIComponent(query)}`).then(setHits), 250); return () => clearTimeout(timer); }, [query]);
  const submit = async (e: FormEvent) => { e.preventDefault(); try { await api("/watchlist", { method: "POST", body: JSON.stringify({ symbol, group_name: form.group_name, note: form.note, target_price: form.target_price ? +form.target_price : null, watch_price: form.watch_price ? +form.watch_price : null }) }); toast.show("已加入自选股"); onSaved(); } catch (error) { toast.show((error as Error).message, "error"); } };
  return <form className="stack-form" onSubmit={submit}><label>搜索股票<div className="search-field"><Search size={15} /><input value={query} onChange={(e) => { setQuery(e.target.value); setSymbol(""); }} placeholder="输入代码或名称" /></div></label>{hits.length > 0 && !symbol && <div className="picker-list">{hits.map((hit) => <button type="button" key={hit.symbol} onClick={() => { setSymbol(hit.symbol); setQuery(`${hit.name || hit.symbol} · ${hit.symbol}`); setHits([]); }}>{hit.name || hit.symbol}<small>{hit.symbol} · {hit.industry || "—"}</small></button>)}</div>}<div className="form-row"><label>分组<input value={form.group_name} onChange={(e) => setForm({ ...form, group_name: e.target.value })} /></label><label>目标价<input type="number" step="0.01" value={form.target_price} onChange={(e) => setForm({ ...form, target_price: e.target.value })} /></label><label>关注价<input type="number" step="0.01" value={form.watch_price} onChange={(e) => setForm({ ...form, watch_price: e.target.value })} /></label></div><label>备注<textarea value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></label><button className="primary" disabled={!symbol}>确认添加</button></form>;
}

function WatchEditForm({ item, onSaved }: { item: WatchlistItem; onSaved: () => void }) { const toast = useToast(); const [form, setForm] = useState({ group_name: item.group_name, note: item.note || "", target_price: item.target_price ? String(item.target_price) : "", watch_price: item.watch_price ? String(item.watch_price) : "" }); const submit = async (e: FormEvent) => { e.preventDefault(); try { await api(`/watchlist/${item.id}`, { method: "PATCH", body: JSON.stringify({ group_name: form.group_name, note: form.note, target_price: form.target_price ? +form.target_price : null, watch_price: form.watch_price ? +form.watch_price : null }) }); toast.show("自选设置已更新"); onSaved(); } catch (error) { toast.show((error as Error).message, "error"); } }; return <form className="stack-form" onSubmit={submit}><div className="form-row"><label>分组<input value={form.group_name} onChange={(e) => setForm({ ...form, group_name: e.target.value })} /></label><label>目标价<input type="number" step="0.01" value={form.target_price} onChange={(e) => setForm({ ...form, target_price: e.target.value })} /></label><label>关注价<input type="number" step="0.01" value={form.watch_price} onChange={(e) => setForm({ ...form, watch_price: e.target.value })} /></label></div><label>备注<textarea value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></label><button className="primary">保存修改</button></form>; }
