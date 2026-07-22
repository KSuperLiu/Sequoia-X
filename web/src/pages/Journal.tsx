import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft, BookOpenCheck, CalendarDays, CheckCircle2, ChevronLeft, ChevronRight,
  CircleDollarSign, FilePenLine, Plus, Save, Search, Sparkles, Trash2,
} from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { api, money } from "../api";
import { Empty, PageHeader, StatusBadge } from "../components/Ui";
import { useToast } from "../components/Toast";

type JournalContext = {
  report?: { id: number; title: string; summary: { action: string } };
  candidate: { count: number; avg_score: number; left_count: number; middle_count: number; right_count: number; veto_count: number };
  fills: Array<{ id: number; symbol: string; side: string; quantity: number; price: number; note?: string; account_name?: string }>;
  snapshots: Array<{ account_id: number; account_name: string; cash: number; market_value: number; equity: number; unrealized_pnl: number; realized_pnl: number; drawdown: number; position_count: number }>;
};

type JournalEntry = {
  id: number;
  review_date: string;
  title: string;
  status: "DRAFT" | "COMPLETED";
  market_phase: keyof typeof phaseLabels;
  emotion: keyof typeof emotionLabels;
  discipline_score: number;
  market_observation: string;
  trade_review: string;
  mistakes: string;
  lessons: string;
  tomorrow_plan: string;
  tags: string[];
  related_symbols: string[];
  created_at: string;
  updated_at: string;
  context?: JournalContext;
};

type JournalList = {
  items: JournalEntry[];
  total: number;
  page: number;
  page_size: number;
  stats: { total: number; completed: number; current_month: number; avg_discipline: number };
  tags: Array<{ tag: string; count: number }>;
};

type JournalForm = {
  review_date: string;
  title: string;
  status: "DRAFT" | "COMPLETED";
  market_phase: keyof typeof phaseLabels;
  emotion: keyof typeof emotionLabels;
  discipline_score: number;
  market_observation: string;
  trade_review: string;
  mistakes: string;
  lessons: string;
  tomorrow_plan: string;
  tags: string[];
  related_symbols: string[];
};

const phaseLabels = { BULL: "强势上涨", REBOUND: "修复反弹", RANGE: "震荡整理", WEAK: "弱势下跌", PANIC: "恐慌释放" };
const emotionLabels = { CALM: "冷静", CONFIDENT: "自信", ANXIOUS: "焦虑", IMPULSIVE: "冲动", FEARFUL: "恐惧" };
const today = () => new Date().toISOString().slice(0, 10);

const emptyForm = (): JournalForm => ({
  review_date: today(), title: `${today()} 交易复盘`, status: "DRAFT",
  market_phase: "RANGE" as keyof typeof phaseLabels,
  emotion: "CALM" as keyof typeof emotionLabels,
  discipline_score: 3, market_observation: "", trade_review: "", mistakes: "",
  lessons: "", tomorrow_plan: "", tags: [] as string[], related_symbols: [] as string[],
});

export default function Journal() {
  const { journalId } = useParams();
  const location = useLocation();
  return journalId || location.pathname.endsWith("/new")
    ? <JournalEditor journalId={journalId ? Number(journalId) : undefined} />
    : <JournalHome />;
}

function JournalHome() {
  const navigate = useNavigate();
  const [data, setData] = useState<JournalList | null>(null);
  const [filters, setFilters] = useState({ q: "", status: "", tag: "", start_date: "", end_date: "" });
  const [page, setPage] = useState(1);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const query = new URLSearchParams({ page: String(page), page_size: "12" });
      Object.entries(filters).forEach(([key, value]) => { if (value) query.set(key, value); });
      api<JournalList>(`/journal?${query}`).then(setData);
    }, 180);
    return () => window.clearTimeout(timer);
  }, [filters, page]);

  const updateFilter = (key: keyof typeof filters, value: string) => {
    setPage(1);
    setFilters((current) => ({ ...current, [key]: value }));
  };
  const pages = Math.max(1, Math.ceil((data?.total || 0) / 12));

  return <>
    <PageHeader
      title="个人复盘日记"
      subtitle="把市场判断、交易行为和下一步计划沉淀为可检索的个人交易档案"
      actions={<button className="primary icon-button" onClick={() => navigate("/journal/new")}><Plus size={16} />写今日复盘</button>}
    />
    <section className="journal-kpis">
      <div><BookOpenCheck /><span>累计复盘</span><b>{data?.stats.total || 0}</b><small>篇个人日记</small></div>
      <div><CheckCircle2 /><span>已完成</span><b>{data?.stats.completed || 0}</b><small>草稿不计入完成</small></div>
      <div><CalendarDays /><span>本月记录</span><b>{data?.stats.current_month || 0}</b><small>保持复盘节奏</small></div>
      <div><Sparkles /><span>平均纪律</span><b>{data?.stats.avg_discipline ? data.stats.avg_discipline.toFixed(1) : "—"}</b><small>满分 5 分</small></div>
    </section>
    <section className="filter-panel journal-filters">
      <label className="search-field"><Search size={15} /><input value={filters.q} onChange={(e) => updateFilter("q", e.target.value)} placeholder="搜索标题、观察或经验" /></label>
      <select aria-label="日记状态" value={filters.status} onChange={(e) => updateFilter("status", e.target.value)}><option value="">全部状态</option><option value="DRAFT">草稿</option><option value="COMPLETED">已完成</option></select>
      <select aria-label="日记标签" value={filters.tag} onChange={(e) => updateFilter("tag", e.target.value)}><option value="">全部标签</option>{data?.tags.map((item) => <option value={item.tag} key={item.tag}>{item.tag} ({item.count})</option>)}</select>
      <input aria-label="开始日期" type="date" value={filters.start_date} onChange={(e) => updateFilter("start_date", e.target.value)} />
      <input aria-label="结束日期" type="date" value={filters.end_date} onChange={(e) => updateFilter("end_date", e.target.value)} />
      <button className="secondary" onClick={() => { setFilters({ q: "", status: "", tag: "", start_date: "", end_date: "" }); setPage(1); }}>重置</button>
    </section>
    {!data ? <div className="skeleton">正在加载个人复盘…</div> : data.items.length === 0 ? <Empty><button className="empty-action" onClick={() => navigate("/journal/new")}><FilePenLine size={24} />还没有符合条件的复盘，开始写第一篇</button></Empty> : <section className="journal-grid">
      {data.items.map((entry) => <button className="journal-card" key={entry.id} onClick={() => navigate(`/journal/${entry.id}`)}>
        <div className="journal-card-head"><time>{entry.review_date}</time><StatusBadge status={entry.status} /></div>
        <h2>{entry.title}</h2>
        <div className="journal-card-meta"><span>{phaseLabels[entry.market_phase]}</span><span>{emotionLabels[entry.emotion]}</span><span>纪律 {entry.discipline_score}/5</span></div>
        <p>{entry.lessons || entry.market_observation || entry.trade_review || "尚未填写复盘内容"}</p>
        <div className="journal-tags">{entry.tags.map((tag) => <span key={tag}>#{tag}</span>)}{entry.related_symbols.map((symbol) => <b key={symbol}>{symbol}</b>)}</div>
        <small>更新于 {new Date(entry.updated_at).toLocaleString("zh-CN")}</small>
      </button>)}
    </section>}
    {data && data.total > 12 && <div className="pagination"><span>共 {data.total} 篇 · 第 {page}/{pages} 页</span><div><button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}><ChevronLeft size={15} />上一页</button><button disabled={page >= pages} onClick={() => setPage((value) => value + 1)}>下一页<ChevronRight size={15} /></button></div></div>}
  </>;
}

function JournalEditor({ journalId }: { journalId?: number }) {
  const navigate = useNavigate();
  const toast = useToast();
  const [form, setForm] = useState(emptyForm());
  const [context, setContext] = useState<JournalContext | null>(null);
  const [tags, setTags] = useState("");
  const [symbols, setSymbols] = useState("");
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(!journalId);

  useEffect(() => {
    if (!journalId) return;
    api<JournalEntry>(`/journal/${journalId}`).then((entry) => {
      setForm({
        review_date: entry.review_date, title: entry.title, status: entry.status,
        market_phase: entry.market_phase, emotion: entry.emotion,
        discipline_score: entry.discipline_score, market_observation: entry.market_observation,
        trade_review: entry.trade_review, mistakes: entry.mistakes, lessons: entry.lessons,
        tomorrow_plan: entry.tomorrow_plan, tags: entry.tags, related_symbols: entry.related_symbols,
      });
      setTags(entry.tags.join(", "));
      setSymbols(entry.related_symbols.join(", "));
      setContext(entry.context || null);
      setLoaded(true);
    }).catch((error) => { toast.show((error as Error).message, "error"); navigate("/journal"); });
  }, [journalId]);

  useEffect(() => {
    if (!loaded) return;
    api<JournalContext>(`/journal/context/${form.review_date}`).then(setContext).catch(() => setContext(null));
  }, [form.review_date, loaded]);

  const payload = (status: "DRAFT" | "COMPLETED") => ({
    ...form, status,
    tags: tags.split(/[,，]/).map((value) => value.trim()).filter(Boolean),
    related_symbols: symbols.split(/[,，\s]+/).map((value) => value.trim()).filter(Boolean),
  });

  const save = async (status: "DRAFT" | "COMPLETED") => {
    if (!form.title.trim()) { toast.show("请填写复盘标题", "error"); return; }
    setSaving(true);
    try {
      if (journalId) {
        await api(`/journal/${journalId}`, { method: "PUT", body: JSON.stringify(payload(status)) });
        setForm((current) => ({ ...current, status }));
        toast.show(status === "COMPLETED" ? "复盘已完成" : "草稿已保存");
      } else {
        const result = await api<{ id: number }>("/journal", { method: "POST", body: JSON.stringify(payload(status)) });
        toast.show(status === "COMPLETED" ? "复盘已完成" : "草稿已保存");
        navigate(`/journal/${result.id}`, { replace: true });
      }
    } catch (error) {
      toast.show((error as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!journalId || !window.confirm("确认删除这篇个人复盘？删除后无法恢复。")) return;
    try {
      await api(`/journal/${journalId}`, { method: "DELETE" });
      toast.show("复盘日记已删除");
      navigate("/journal");
    } catch (error) {
      toast.show((error as Error).message, "error");
    }
  };

  const useTemplate = () => setForm((current) => ({
    ...current,
    market_observation: current.market_observation || "指数与成交量：\n主线与轮动：\n风险信号：",
    trade_review: current.trade_review || "今日执行：\n符合计划的操作：\n计划外操作：",
    mistakes: current.mistakes || "认知错误：\n执行错误：\n情绪干扰：",
    lessons: current.lessons || "今天验证了什么：\n下次要坚持或改变什么：",
    tomorrow_plan: current.tomorrow_plan || "重点观察：\n触发条件：\n仓位上限：\n明确不做：",
  }));

  if (!loaded) return <div className="skeleton">正在打开个人复盘…</div>;
  return <>
    <button className="back-link" onClick={() => navigate("/journal")}><ArrowLeft size={15} />返回复盘日记</button>
    <PageHeader
      title={journalId ? form.title : "新建个人复盘"}
      subtitle={journalId ? `${form.review_date} · 最后保存后可继续修订` : "建议在收盘后结合当日数据完成，先记录事实，再总结判断"}
      actions={<div className="page-actions"><button className="secondary icon-button" onClick={useTemplate}><Sparkles size={15} />使用模板</button>{journalId && <button className="secondary icon-button danger-text" onClick={() => void remove()}><Trash2 size={15} />删除</button>}<button disabled={saving} className="secondary icon-button" onClick={() => void save("DRAFT")}><Save size={15} />保存草稿</button><button disabled={saving} className="primary icon-button" onClick={() => void save("COMPLETED")}><CheckCircle2 size={15} />完成复盘</button></div>}
    />
    <div className="journal-editor-layout">
      <form className="panel journal-form" onSubmit={(event: FormEvent) => { event.preventDefault(); void save("DRAFT"); }}>
        <div className="journal-basic-grid">
          <label>复盘日期<input type="date" value={form.review_date} onChange={(e) => setForm({ ...form, review_date: e.target.value, title: form.title === `${form.review_date} 交易复盘` ? `${e.target.value} 交易复盘` : form.title })} /></label>
          <label className="journal-title-field">标题<input maxLength={100} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="用一句话概括今天" /></label>
          <label>市场阶段<select value={form.market_phase} onChange={(e) => setForm({ ...form, market_phase: e.target.value as keyof typeof phaseLabels })}>{Object.entries(phaseLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <label>交易情绪<select value={form.emotion} onChange={(e) => setForm({ ...form, emotion: e.target.value as keyof typeof emotionLabels })}>{Object.entries(emotionLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        </div>
        <div className="discipline-field"><span>纪律执行评分</span><div>{[1, 2, 3, 4, 5].map((score) => <button type="button" className={form.discipline_score === score ? "active" : ""} key={score} onClick={() => setForm({ ...form, discipline_score: score })}>{score}</button>)}</div><small>只评价是否遵守计划，不评价当天盈亏</small></div>
        <JournalTextarea title="市场观察" hint="记录指数、量能、主线、情绪和风险，不急于下结论" value={form.market_observation} onChange={(value) => setForm({ ...form, market_observation: value })} />
        <JournalTextarea title="交易复盘" hint="哪些操作符合计划？哪些临时起意？结果与过程分开评价" value={form.trade_review} onChange={(value) => setForm({ ...form, trade_review: value })} />
        <JournalTextarea title="错误与偏差" hint="认知、执行、仓位或情绪上出现了什么问题" value={form.mistakes} onChange={(value) => setForm({ ...form, mistakes: value })} tone="danger" />
        <JournalTextarea title="经验与收获" hint="把可复用的经验写成下次能执行的规则" value={form.lessons} onChange={(value) => setForm({ ...form, lessons: value })} tone="success" />
        <JournalTextarea title="明日计划" hint="关注标的、触发条件、仓位上限和明确不做的事情" value={form.tomorrow_plan} onChange={(value) => setForm({ ...form, tomorrow_plan: value })} tone="primary" />
        <div className="journal-basic-grid journal-meta-fields"><label>标签<input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="例如：趋势, 纪律, 追高（逗号分隔）" /></label><label>关联股票<input value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="例如：600519 000001" /></label></div>
      </form>
      <aside className="journal-context"><ContextPanel context={context} navigate={navigate} /><section className="panel journal-checklist"><h3>复盘顺序</h3><ol><li>先记录市场与交易事实</li><li>再判断计划是否被执行</li><li>区分坏结果与坏决策</li><li>最后写出可验证的明日计划</li></ol></section></aside>
    </div>
  </>;
}

function JournalTextarea({ title, hint, value, onChange, tone = "" }: { title: string; hint: string; value: string; onChange: (value: string) => void; tone?: string }) {
  return <label className={`journal-section ${tone}`}><span><b>{title}</b><small>{hint}</small></span><textarea maxLength={10_000} value={value} onChange={(e) => onChange(e.target.value)} rows={6} /></label>;
}

function ContextPanel({ context, navigate }: { context: JournalContext | null; navigate: ReturnType<typeof useNavigate> }) {
  const candidate = context?.candidate;
  const totalFills = useMemo(() => context?.fills.length || 0, [context]);
  return <section className="panel context-panel">
    <div className="panel-title"><div><CircleDollarSign size={17} /><h2>当日系统数据</h2></div></div>
    {!context ? <p className="muted">该日期暂无可关联的数据。</p> : <>
      <div className="context-metrics"><div><span>候选</span><b>{candidate?.count || 0}</b></div><div><span>平均分</span><b>{candidate?.avg_score?.toFixed(1) || "—"}</b></div><div><span>个人成交</span><b>{totalFills}</b></div></div>
      <div className="context-zones"><span className="positive">左 {candidate?.left_count || 0}</span><span>中 {candidate?.middle_count || 0}</span><span className="negative">右 {candidate?.right_count || 0}</span><span className="negative">否决 {candidate?.veto_count || 0}</span></div>
      {context.report && <button className="context-link" onClick={() => navigate(`/reports/${context.report?.id}`)}><span><b>{context.report.title}</b><small>{context.report.summary.action}</small></span><ChevronRight size={16} /></button>}
      {context.fills.length > 0 && <div className="context-list"><h3>当日成交</h3>{context.fills.map((fill) => <div key={fill.id}><span><b>{fill.symbol}</b><small>{fill.account_name || "个人账户"}</small></span><span className={fill.side === "BUY" ? "negative" : "positive"}>{fill.side === "BUY" ? "买入" : "卖出"} {fill.quantity} 股<small>¥ {money(fill.price)}</small></span></div>)}</div>}
      {context.snapshots.length > 0 && <div className="context-list"><h3>账户快照</h3>{context.snapshots.map((snapshot) => <div key={snapshot.account_id}><span><b>{snapshot.account_name}</b><small>{snapshot.position_count} 只持仓</small></span><span>权益 ¥ {money(snapshot.equity)}<small>回撤 {(snapshot.drawdown * 100).toFixed(1)}%</small></span></div>)}</div>}
      {!context.report && context.fills.length === 0 && context.snapshots.length === 0 && !candidate?.count && <p className="muted">当天暂无系统日报、成交或账户快照，仍可独立填写复盘。</p>}
    </>}
  </section>;
}
