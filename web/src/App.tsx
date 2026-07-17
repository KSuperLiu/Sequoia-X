import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { BarChart3, BellRing, BriefcaseBusiness, ClipboardList, FlaskConical, LayoutDashboard, ListChecks, LogOut, Search, Settings, Star, UserRound } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { api, setCsrfToken } from "./api";
import Login from "./pages/Login";

const Backtests = lazy(() => import("./pages/Backtests"));
const Candidates = lazy(() => import("./pages/Candidates"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Plans = lazy(() => import("./pages/Plans"));
const PortfolioPage = lazy(() => import("./pages/Portfolio"));
const Reports = lazy(() => import("./pages/Reports"));
const SettingsPage = lazy(() => import("./pages/Settings"));
const StockDetail = lazy(() => import("./pages/StockDetail"));
const Tasks = lazy(() => import("./pages/Tasks"));

type User = { username: string; csrf_token: string };
type StockHit = { symbol: string; name?: string; industry?: string; snapshot?: { close?: number; date?: string } };

const groups: Array<{ label: string; items: Array<[string, string, LucideIcon]> }> = [
  { label: "决策", items: [["/dashboard", "工作台", LayoutDashboard], ["/candidates", "机会中心", Star], ["/plans", "计划中心", ClipboardList]] },
  { label: "交易", items: [["/portfolio", "组合账户", BriefcaseBusiness]] },
  { label: "研究", items: [["/reports", "复盘分析", BarChart3], ["/backtests", "回测研究", FlaskConical]] },
  { label: "系统", items: [["/tasks", "任务中心", ListChecks], ["/settings", "系统管理", Settings]] },
];

function GlobalSearch() {
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<StockHit[]>([]);
  const navigate = useNavigate();
  useEffect(() => {
    if (!query.trim()) { setRows([]); return; }
    const timer = window.setTimeout(() => api<StockHit[]>(`/stocks/search?q=${encodeURIComponent(query.trim())}`).then(setRows).catch(() => setRows([])), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  const open = (symbol: string) => { setQuery(""); setRows([]); navigate(`/stocks/${symbol}`); };
  return <div className="global-search"><Search size={16} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索股票代码或名称" onKeyDown={(e) => { if (e.key === "Enter" && rows[0]) open(rows[0].symbol); }} />{rows.length > 0 && <div className="search-results">{rows.map((row) => <button key={row.symbol} onClick={() => open(row.symbol)}><span><b>{row.name || row.symbol}</b><small>{row.symbol} · {row.industry || "行业待补充"}</small></span><span>{row.snapshot?.close ?? "—"}<small>{row.snapshot?.date || "暂无快照"}</small></span></button>)}</div>}</div>;
}

function Shell({ user, onLogout }: { user: User; onLogout: () => Promise<void> }) {
  const navigate = useNavigate();
  const location = useLocation();
  const currentLabel = groups.flatMap((group) => group.items).find(([path]) => location.pathname.startsWith(path))?.[1] || (location.pathname.startsWith("/stocks/") ? "股票详情" : "工作台");
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">S</span><div><b>Sequoia-X</b><small>专业投研工作台</small></div></div>
      <nav>{groups.map((group) => <section key={group.label}><label>{group.label}</label>{group.items.map(([to, label, Icon]) => <NavLink key={to} to={to} className={({ isActive }) => isActive ? "active" : ""}><Icon size={17} />{label}</NavLink>)}</section>)}</nav>
      <div className="sidebar-foot"><div className="avatar">{user.username.slice(0, 1).toUpperCase()}</div><div><b>{user.username}</b><small>单管理员</small></div><button onClick={async () => { await onLogout(); navigate("/login"); }} title="退出登录"><LogOut size={17} /></button></div>
    </aside>
    <main className="content"><header className="topbar"><div className="breadcrumb"><span>Sequoia-X</span><b>/</b><strong>{currentLabel}</strong></div><GlobalSearch /><div className="topbar-actions"><button title="查看风险提醒" onClick={() => navigate("/dashboard")}><BellRing size={17} /></button><button title="管理员设置" onClick={() => navigate("/settings")}><UserRound size={17} /><span>{user.username}</span></button></div></header><div className="page-body"><Suspense fallback={<div className="skeleton">正在载入页面…</div>}><Routes>
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/candidates" element={<Candidates />} />
      <Route path="/plans" element={<Plans />} />
      <Route path="/portfolio" element={<PortfolioPage />} />
      <Route path="/reports" element={<Reports />} />
      <Route path="/reports/:reportId" element={<Reports />} />
      <Route path="/backtests" element={<Backtests />} />
      <Route path="/backtests/:runId" element={<Backtests />} />
      <Route path="/stocks/:symbol" element={<StockDetail />} />
      <Route path="/tasks" element={<Tasks />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes></Suspense></div></main>
  </div>;
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => { api<User>("/auth/me").then((value) => { setCsrfToken(value.csrf_token); setUser(value); }).catch(() => setUser(null)).finally(() => setLoading(false)); }, []);
  if (loading) return <div className="loading-screen"><span className="brand-mark">S</span><p>正在载入投资工作台…</p></div>;
  return <Routes><Route path="/login" element={user ? <Navigate to="/dashboard" replace /> : <Login onLogin={(value) => { setCsrfToken(value.csrf_token); setUser(value); }} />} /><Route path="/*" element={user ? <Shell user={user} onLogout={async () => { await api("/auth/logout", { method: "POST" }); setUser(null); }} /> : <Navigate to="/login" replace />} /></Routes>;
}
