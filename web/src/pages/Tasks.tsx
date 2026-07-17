import { useEffect, useState } from "react";
import { Coins, History, Play, RefreshCw, RotateCcw, ScrollText, Square } from "lucide-react";
import { api } from "../api";
import { Modal, PageHeader, StatusBadge } from "../components/Ui";
import { useToast } from "../components/Toast";
import { JobRun } from "../types";

const jobMeta = {
  DAILY_UPDATE: { title: "日常数据更新", description: "拉取最新日线、执行策略、生成候选与日报", icon: RefreshCw },
  REFRESH_MARKET_CAP: { title: "刷新市值表", description: "联网更新本地市值快照，日跑本身不会自动刷新", icon: Coins },
  BACKFILL: { title: "历史行情回填", description: "按当前市值股票池回填历史 K 线，耗时较长", icon: History },
} as const;

const shownStatus = (job: JobRun) => job.status === "RUNNING" && job.cancel_requested ? "CANCELLING" : job.status;
const canCancel = (job: JobRun) => job.source === "MANUAL" && ["PENDING", "RUNNING"].includes(job.status) && !job.cancel_requested;

export default function Tasks() {
  const [rows, setRows] = useState<JobRun[]>([]);
  const [selected, setSelected] = useState<JobRun | null>(null);
  const toast = useToast();
  const load = () => api<JobRun[]>("/jobs").then(setRows);

  useEffect(() => {
    void load();
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, []);

  const busy = rows.some((row) => ["PENDING", "RUNNING"].includes(row.status));
  const trigger = async (jobType: keyof typeof jobMeta) => {
    if (jobType === "REFRESH_MARKET_CAP" && !window.confirm("确认联网刷新本地市值表？完成后建议再执行历史回填。")) return;
    let confirmation = "";
    if (jobType === "BACKFILL") {
      confirmation = window.prompt("历史回填耗时较长，请输入 BACKFILL 确认：") || "";
      if (confirmation !== "BACKFILL") return;
    }
    try {
      await api("/jobs", { method: "POST", body: JSON.stringify({ job_type: jobType, confirmation }) });
      toast.show("任务已加入串行队列");
      void load();
    } catch (e) {
      toast.show((e as Error).message, "error");
    }
  };
  const detail = async (id: number) => setSelected(await api<JobRun>(`/jobs/${id}`));
  const retry = async (id: number) => {
    try {
      await api(`/jobs/${id}/retry`, { method: "POST" });
      toast.show("重试任务已加入队列");
      setSelected(null);
      void load();
    } catch (e) {
      toast.show((e as Error).message, "error");
    }
  };
  const cancel = async (job: JobRun) => {
    const title = jobMeta[job.job_type as keyof typeof jobMeta]?.title || job.job_type;
    if (!window.confirm(`确认中止任务 #${job.id} · ${title}？\n运行中的子进程将被终止，已写入的数据不会回滚。`)) return;
    try {
      const result = await api<JobRun>(`/jobs/${job.id}/cancel`, { method: "POST" });
      toast.show(result.status === "CANCELLED" ? "等待任务已取消" : "中止请求已发送，正在停止任务进程");
      setSelected(null);
      void load();
    } catch (e) {
      toast.show((e as Error).message, "error");
    }
  };

  return <>
    <PageHeader title="任务中心" subtitle="所有数据维护任务串行执行，避免低内存服务器同时运行重任务" />
    <section className="job-actions">{Object.entries(jobMeta).map(([type, meta]) => {
      const Icon = meta.icon;
      const latest = rows.find((row) => row.job_type === type);
      return <article className="panel" key={type}>
        <div className="job-icon"><Icon size={20} /></div>
        <div><h3>{meta.title}</h3><p>{meta.description}</p></div>
        {latest && <div className="job-last"><span>最近执行</span><StatusBadge status={shownStatus(latest)} /></div>}
        <button className="primary icon-button" disabled={busy} onClick={() => trigger(type as keyof typeof jobMeta)}><Play size={14} />{busy ? "队列忙" : "开始任务"}</button>
      </article>;
    })}</section>
    <section className="table-panel">
      <div className="panel-title"><div><ScrollText size={17} /><h2>任务历史</h2><small>阶段、进度、退出码和完整日志</small></div></div>
      <div className="table-scroll"><table><thead><tr><th>ID</th><th>任务</th><th>来源</th><th>状态</th><th>阶段 / 进度</th><th>说明</th><th>请求时间</th><th>完成时间</th><th>操作</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}>
        <td>#{row.id}</td><td><b>{jobMeta[row.job_type as keyof typeof jobMeta]?.title || row.job_type}</b></td><td>{row.source}</td>
        <td><StatusBadge status={shownStatus(row)} /></td><td>{row.current_stage || "等待启动"}{row.progress_total > 0 && <small>{row.progress_current}/{row.progress_total}</small>}</td>
        <td className="job-message">{row.message || "—"}</td><td>{new Date(row.requested_at).toLocaleString("zh-CN")}</td><td>{row.finished_at ? new Date(row.finished_at).toLocaleString("zh-CN") : "—"}</td>
        <td><div className="row-actions"><button className="text-button" onClick={() => detail(row.id)}>日志</button>{canCancel(row) && <button className="danger-text" title="中止任务" onClick={() => void cancel(row)}><Square size={14} />中止</button>}</div></td>
      </tr>)}</tbody></table></div>
    </section>
    {selected && <Modal title={`任务 #${selected.id} · ${jobMeta[selected.job_type as keyof typeof jobMeta]?.title || selected.job_type}`} onClose={() => setSelected(null)} wide>
      <div className="job-detail"><div className="comparison-grid"><div><span>状态</span><StatusBadge status={shownStatus(selected)} /><small>{selected.current_stage || "—"}</small></div><div><span>退出码</span><b>{selected.exit_code ?? "—"}</b><small>{selected.message}</small></div></div>
        {selected.progress_total > 0 && <div className="progress"><i style={{ width: `${selected.progress_current / selected.progress_total * 100}%` }} /></div>}
        <h3>执行日志</h3><pre className="log-view">{selected.logs?.slice().reverse().map((log) => `[${log.level}] ${log.message}`).join("\n") || "暂无日志"}</pre>
        <div className="security-actions">{canCancel(selected) && <button className="secondary icon-button danger-text" onClick={() => void cancel(selected)}><Square size={15} />中止任务</button>}{["FAILED", "PARTIAL", "CANCELLED"].includes(selected.status) && <button className="primary icon-button" onClick={() => retry(selected.id)}><RotateCcw size={15} />重试任务</button>}</div>
      </div>
    </Modal>}
  </>;
}
