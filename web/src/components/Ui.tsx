import { ReactNode } from "react";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle: string; actions?: ReactNode }) {
  return <header className="page-header"><div><span className="eyebrow">SEQUOIA-X</span><h1>{title}</h1><p>{subtitle}</p></div>{actions && <div className="page-actions">{actions}</div>}</header>;
}

export function Kpi({ label, value, tone = "navy", note }: { label: string; value: ReactNode; tone?: string; note?: string }) {
  return <div className={`kpi ${tone}`}><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</div>;
}

export function Empty({ children }: { children: ReactNode }) { return <div className="empty"><div>◇</div><p>{children}</p></div>; }

export function ZoneBadge({ zone }: { zone: string }) {
  const labels: Record<string, string> = { LEFT: "左侧", MIDDLE: "中部", RIGHT: "右侧", VETO: "否决" };
  return <span className={`zone ${zone.toLowerCase()}`}><i />{labels[zone] || zone}</span>;
}
