import { ReactNode } from "react";
import { Inbox, X } from "lucide-react";

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle: string; actions?: ReactNode }) {
  return <header className="page-header"><div><span className="eyebrow">SEQUOIA-X</span><h1>{title}</h1><p>{subtitle}</p></div>{actions && <div className="page-actions">{actions}</div>}</header>;
}

export function Kpi({ label, value, tone = "navy", note, onClick }: { label: string; value: ReactNode; tone?: string; note?: string; onClick?: () => void }) {
  const content = <><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</>;
  return onClick ? <button type="button" className={`kpi ${tone} clickable`} onClick={onClick}>{content}</button> : <div className={`kpi ${tone}`}>{content}</div>;
}

export function Empty({ children }: { children: ReactNode }) { return <div className="empty"><Inbox size={30} /><p>{children}</p></div>; }

export function ZoneBadge({ zone }: { zone: string }) {
  const labels: Record<string, string> = { LEFT: "左侧", MIDDLE: "中部", RIGHT: "右侧", VETO: "否决" };
  return <span className={`zone ${zone.toLowerCase()}`}><i />{labels[zone] || zone}</span>;
}

export function Modal({ title, onClose, children, wide = false }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  return <div className="drawer-backdrop centered" onMouseDown={onClose}><section className={`modal ${wide ? "modal-wide" : ""}`} onMouseDown={(event) => event.stopPropagation()}><button className="drawer-close" onClick={onClose} aria-label="关闭"><X size={18} /></button><h2>{title}</h2>{children}</section></div>;
}

export function StatusBadge({ status }: { status: string }) {
  const labels: Record<string, string> = { PENDING: "等待中", RUNNING: "运行中", CANCELLING: "正在中止", SUCCEEDED: "成功", PARTIAL: "部分成功", FAILED: "失败", DRAFT: "草稿", READY: "待执行", EXECUTED: "已执行", CANCELLED: "已取消", EXPIRED: "已过期" };
  return <span className={`status ${status === "CANCELLING" ? "running" : status.toLowerCase()}`}>{labels[status] || status}</span>;
}
