import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { PageHeader } from "../components/Ui";

type Settings = { daily_run_time: string; min_market_cap: number; public_base_url: string; supported_strategies: string[] };
type Run = { id: number; trade_date: string; status: string; candidate_count: number; message?: string; started_at: string; finished_at?: string };
type DailyJob = {
  status: "IDLE" | "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED";
  source?: "MANUAL" | "SCHEDULED";
  requested_at?: string;
  started_at?: string;
  finished_at?: string;
  message?: string;
};

const ACTIVE_STATUSES = new Set(["PENDING", "RUNNING"]);

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [job, setJob] = useState<DailyJob | null>(null);
  const [message, setMessage] = useState("");
  const [triggering, setTriggering] = useState(false);

  const load = () => {
    api<Settings>("/settings").then(setSettings);
    api<Run[]>("/runs").then(setRuns);
    api<DailyJob>("/runs/status").then(setJob);
  };

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, []);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!settings) return;
    await api("/settings", {
      method: "PUT",
      body: JSON.stringify({ daily_run_time: settings.daily_run_time, min_market_cap: settings.min_market_cap }),
    });
    setMessage("设置已保存。市值阈值变化后请手动刷新市值并回填历史数据。");
  };

  const trigger = async () => {
    setTriggering(true);
    setMessage("");
    try {
      const next = await api<DailyJob>("/runs/trigger", { method: "POST" });
      setJob(next);
      setMessage("更新请求已提交，Worker 将在30秒内开始执行。页面会自动刷新状态。");
    } catch (reason) {
      setMessage((reason as Error).message);
    } finally {
      setTriggering(false);
    }
  };

  const busy = triggering || (job ? ACTIVE_STATUSES.has(job.status) : false);
  return <>
    <PageHeader title="系统与任务设置" subtitle="修改日程和股票池门槛；敏感凭据仅通过服务器环境变量管理" />
    {settings && <form className="panel settings-form" onSubmit={save}>
      <div><h2>日常任务</h2><p>默认仅在交易日收盘后运行，不在日跑中联网刷新市值。</p></div>
      <label>日跑时间（Asia/Shanghai）<input type="time" value={settings.daily_run_time} onChange={(event) => setSettings({ ...settings, daily_run_time: event.target.value })} /></label>
      <label>最低总市值（元）<input type="number" step="100000000" value={settings.min_market_cap} onChange={(event) => setSettings({ ...settings, min_market_cap: +event.target.value })} /></label>
      <label>公网地址<input value={settings.public_base_url} disabled /></label>
      <button className="primary">保存设置</button>
    </form>}
    <section className="panel">
      <div className="panel-title">
        <div><h2>手动更新数据</h2><small>立即拉取最新行情、执行策略并生成追踪日报</small></div>
        <button className="primary" type="button" onClick={trigger} disabled={busy}>
          {job?.status === "RUNNING" ? "正在更新…" : job?.status === "PENDING" ? "等待执行…" : triggering ? "正在提交…" : "立即更新数据"}
        </button>
      </div>
      <div className="decision-line"><span>任务状态</span><b className={`status ${(job?.status || "idle").toLowerCase()}`}>{job?.status || "IDLE"}</b></div>
      <div className="decision-line"><span>触发来源</span><b>{job?.source || "—"}</b></div>
      <div className="decision-line"><span>任务说明</span><b>{job?.message || "尚未执行"}</b></div>
      {message && <div className="warning-banner">{message}</div>}
    </section>
    <section className="table-panel">
      <div className="panel-title"><div><h2>最近任务</h2><small>运行状态、数据完整性与候选数量</small></div></div>
      <div className="table-scroll"><table><thead><tr><th>交易日</th><th>状态</th><th>候选</th><th>说明</th><th>开始</th><th>结束</th></tr></thead><tbody>{runs.map((run) => <tr key={run.id}><td>{run.trade_date}</td><td><span className={`status ${run.status.toLowerCase()}`}>{run.status}</span></td><td>{run.candidate_count}</td><td>{run.message || "—"}</td><td>{run.started_at}</td><td>{run.finished_at || "—"}</td></tr>)}</tbody></table></div>
    </section>
  </>;
}
