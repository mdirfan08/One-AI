import { ReactNode, useState } from "react";
import type { TaskStatus } from "./types";

export const STATUS_LABEL: Record<string, string> = {
  planned: "Planned", waiting: "Waiting", running: "Running", needs_approval: "Needs approval", completed: "Completed",
  failed: "Failed", blocked: "Blocked", draft: "Draft", planning: "Planning", planned_p: "Planned", paused: "Paused",
  partial: "Partial", cancelled: "Cancelled", budget_stopped: "Budget stop",
};

export function Badge({ status, children }: { status?: string; children?: ReactNode }) {
  return <span className={`badge s-${status ?? "none"}`}>{children ?? STATUS_LABEL[status ?? ""] ?? status}</span>;
}
export const Sim = () => <span className="badge sim" title="Produced by the built-in demo provider, not a real AI model">SIMULATED</span>;

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && <header className="card-h"><h3>{title}</h3><div className="row">{actions}</div></header>}
      {children}
    </section>
  );
}
export const Banner = ({ kind = "info", children }: { kind?: "info" | "warn" | "error"; children: ReactNode }) =>
  <div className={`banner b-${kind}`} role={kind === "error" ? "alert" : undefined}>{children}</div>;

export function Copy({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return <button className="btn sm" onClick={() => { navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1200); }}>{done ? "Copied" : label}</button>;
}
export const isActive = (s: TaskStatus) => s === "running";
export const fmtTime = (t?: number | null) => (t ? new Date(t * 1000).toLocaleTimeString() : "-");
