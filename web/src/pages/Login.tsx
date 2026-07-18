import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { User } from "../auth";

export default function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError("");
    if (mode === "register" && password !== confirm) { setError("两次输入的密码不一致"); setBusy(false); return; }
    try {
      const user = await api<User>(`/auth/${mode}`, { method: "POST", body: JSON.stringify({ username, password }) });
      onLogin(user);
    } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  };
  return <div className="login-page"><section className="login-story"><div className="brand light"><span className="brand-mark">S</span><div><b>Sequoia-X</b><small>A 股决策工作台</small></div></div><div className="story-copy"><span className="eyebrow">END-OF-DAY INTELLIGENCE</span><h1>把每一次选股，<br />变成可追踪的决策。</h1><p>注册个人账号即可创建独立模拟组合，自选、成交和资金记录都只跟随自己的账号。</p><div className="story-metrics"><div><b>8</b><span>四维满分</span></div><div><b>1%</b><span>单笔风险</span></div><div><b>60%</b><span>仓位上限</span></div></div></div></section><section className="login-panel"><form onSubmit={submit}><span className="eyebrow">PERSONAL ACCESS</span><h2>{mode === "login" ? "登录账号" : "注册个人账号"}</h2><p>{mode === "login" ? "登录后使用个人自选与模拟交易" : "无需手机号或邮箱，只校验账号 ID 是否重复"}</p><div className="segmented auth-mode"><button type="button" className={mode === "login" ? "active" : ""} onClick={() => { setMode("login"); setError(""); }}>登录</button><button type="button" className={mode === "register" ? "active" : ""} onClick={() => { setMode("register"); setError(""); }}>注册</button></div><label>账号 ID<input value={username} minLength={mode === "register" ? 3 : 1} maxLength={32} pattern={mode === "register" ? "[A-Za-z0-9_-]+" : undefined} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus /></label><label>密码<input type="password" minLength={mode === "register" ? 8 : 1} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "register" ? "new-password" : "current-password"} /></label>{mode === "register" && <label>确认密码<input type="password" minLength={8} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" /></label>}{error && <div className="error-box">{error}</div>}<button className="primary wide" disabled={busy}>{busy ? "正在处理…" : mode === "login" ? "登录" : "注册并登录"}</button><Link className="secondary wide login-browse" to="/dashboard">暂不登录，继续浏览</Link><small className="login-hint">个人账号仅开放自选、模拟组合与研究功能</small></form></section></div>;
}
