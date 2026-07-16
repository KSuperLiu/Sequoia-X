import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, setCsrfToken } from "./api";
import Login from "./pages/Login";

const Backtests = lazy(() => import("./pages/Backtests"));
const Candidates = lazy(() => import("./pages/Candidates"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const PortfolioPage = lazy(() => import("./pages/Portfolio"));
const Reports = lazy(() => import("./pages/Reports"));
const SettingsPage = lazy(() => import("./pages/Settings"));

type User = { username: string; csrf_token: string };

function Shell({ user, onLogout }: { user: User; onLogout: () => Promise<void> }) {
  const navigate = useNavigate();
  const logout = async () => {
    await onLogout();
    navigate("/login");
  };
  const nav = [
    ["/dashboard", "总览", "⌁"],
    ["/candidates", "候选追踪", "◎"],
    ["/portfolio", "组合交易", "◫"],
    ["/reports", "复盘日报", "▤"],
    ["/backtests", "策略回测", "⌗"],
    ["/settings", "系统设置", "⚙"]
  ];
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">S</span><div><b>Sequoia-X</b><small>决策与交易追踪</small></div></div>
        <nav>{nav.map(([to, label, icon]) => <NavLink key={to} to={to} className={({ isActive }) => isActive ? "active" : ""}><span>{icon}</span>{label}</NavLink>)}</nav>
        <div className="sidebar-foot"><div className="avatar">{user.username.slice(0, 1).toUpperCase()}</div><div><b>{user.username}</b><small>单管理员</small></div><button onClick={logout} title="退出">↗</button></div>
      </aside>
      <main className="content"><Suspense fallback={<div className="skeleton">正在载入页面…</div>}><Routes>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/candidates" element={<Candidates />} />
        <Route path="/portfolio" element={<PortfolioPage />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/backtests" element={<Backtests />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes></Suspense></main>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    api<User>("/auth/me").then((value) => { setCsrfToken(value.csrf_token); setUser(value); }).catch(() => setUser(null)).finally(() => setLoading(false));
  }, []);
  if (loading) return <div className="loading-screen"><span className="brand-mark">S</span><p>正在载入投资工作台…</p></div>;
  return <Routes>
    <Route path="/login" element={user ? <Navigate to="/dashboard" replace /> : <Login onLogin={(value) => { setCsrfToken(value.csrf_token); setUser(value); }} />} />
    <Route path="/*" element={user ? <Shell user={user} onLogout={async () => { await api("/auth/logout", { method: "POST" }); setUser(null); }} /> : <Navigate to="/login" replace />} />
  </Routes>;
}
