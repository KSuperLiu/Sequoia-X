import { FormEvent, useState } from "react";
import { api } from "../api";

export default function Login({ onLogin }: { onLogin: (user: { username: string; csrf_token: string }) => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const user = await api<{ username: string; csrf_token: string }>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      onLogin(user);
    } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  };
  return <div className="login-page"><section className="login-story"><div className="brand light"><span className="brand-mark">S</span><div><b>Sequoia-X</b><small>A 股决策工作台</small></div></div><div className="story-copy"><span className="eyebrow">END-OF-DAY INTELLIGENCE</span><h1>把每一次选股，<br />变成可追踪的决策。</h1><p>四维评分、交易计划、组合风控与历史复盘，在同一个可信闭环里完成。</p><div className="story-metrics"><div><b>8</b><span>四维满分</span></div><div><b>1%</b><span>单笔风险</span></div><div><b>60%</b><span>仓位上限</span></div></div></div></section><section className="login-panel"><form onSubmit={submit}><span className="eyebrow">SECURE ACCESS</span><h2>欢迎回来</h2><p>登录您的单管理员工作台</p><label>管理员账号<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" /></label><label>密码<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus /></label>{error && <div className="error-box">{error}</div>}<button className="primary wide" disabled={busy}>{busy ? "正在验证…" : "安全登录"}</button><small className="login-hint">仅通过加密会话访问，不连接券商自动下单</small></form></section></div>;
}
