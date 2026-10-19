import { useState } from "react";
import { api } from "../api";
import type { Project, Task } from "../types";
import { Badge, Banner, Card, Copy, Sim, fmtTime } from "../ui";
import { computeLayers } from "../lib/graph";

const W = 220, H = 92, GX = 70, GY = 22;

function Handoff({ project, task, onChange }: { project: Project; task: Task; onChange: (p: Project) => void }) {
  const [h, setH] = useState<{ text: string; providers: Record<string, string>; note: string } | null>(null);
  const [result, setResult] = useState("");
  const [err, setErr] = useState("");
  return (
    <div className="stack">
      <Banner kind="warn"><b>Manual handoff.</b> {task.error ?? task.routing_reason}</Banner>
      <div className="row wrap">
        <button className="btn" onClick={async () => setH(await api.handoff(project.id, task.id))}>Show prompt to copy</button>
        {h && <Copy text={h.text} label="Copy prompt" />}
        {h && Object.entries(h.providers).map(([k, u]) => <a key={k} className="btn sm" href={u} target="_blank" rel="noreferrer">Open {k}</a>)}
      </div>
      {h && <><pre className="pre">{h.text}</pre><p className="muted small">{h.note}</p></>}
      <label>Paste the result here (it will be stored as a manual artifact and passed to later stages)</label>
      <textarea rows={5} value={result} onChange={(e) => setResult(e.target.value)} />
      {err && <Banner kind="error">{err}</Banner>}
      <div className="row end">
        <button className="btn" onClick={async () => { try { onChange(await api.skipTask(project.id, task.id)); } catch (e) { setErr((e as Error).message); } }}>Skip task</button>
        <button className="btn primary" disabled={!result.trim()} onClick={async () => { try { onChange(await api.manualResult(project.id, task.id, result)); } catch (e) { setErr((e as Error).message); } }}>Submit result &amp; resume</button>
      </div>
    </div>
  );
}

function Detail({ project, task, onChange }: { project: Project; task: Task; onChange: (p: Project) => void }) {
  const [err, setErr] = useState("");
  const act = async (fn: () => Promise<Project>) => { setErr(""); try { onChange(await fn()); } catch (e) { setErr((e as Error).message); } };
  const awaitingManual = task.status === "blocked" && task.handoff;
  return (
    <Card title={<>{task.title} <Badge status={task.status} /> {task.simulated && <Sim />}</>}
      actions={<>
        {task.status === "needs_approval" && <button className="btn primary" onClick={() => act(() => api.approveTask(project.id, task.id))}>Approve &amp; run</button>}
        {(task.status === "failed" || (task.status === "blocked" && !task.handoff && task.error_code !== "skipped")) && <button className="btn" onClick={() => act(() => api.retryTask(project.id, task.id))}>Retry ({task.manual_retries}/{project.limits.max_retries})</button>}
        {task.status === "needs_approval" && <button className="btn" onClick={() => act(() => api.skipTask(project.id, task.id))}>Skip</button>}
      </>}>
      {err && <Banner kind="error">{err}</Banner>}
      {task.error && !awaitingManual && <Banner kind={task.status === "failed" ? "error" : "warn"}>{task.error_code}: {task.error}</Banner>}
      {awaitingManual && <Handoff project={project} task={task} onChange={onChange} />}
      <div className="kv"><span>Provider</span><b>{task.provider ?? "none"} {task.model ? `/ ${task.model}` : ""}</b></div>
      <div className="kv"><span>Routing</span><span className="small">{task.routing_reason}</span></div>
      <h4>Retry history</h4>
      {task.attempts.length === 0 ? <p className="muted small">No attempts yet.</p> :
        <table className="tbl"><thead><tr><th>#</th><th>Provider</th><th>Result</th><th>Tokens in/out</th><th>Time</th></tr></thead>
          <tbody>{task.attempts.map((a) => <tr key={a.n}><td>{a.n}</td><td>{a.provider}{a.simulated ? " (sim)" : ""}</td>
            <td>{a.ok ? "ok" : <span className="err">{a.error_code}: {a.error?.slice(0, 140)}</span>}</td>
            <td>{a.input_tokens ?? "-"} / {a.output_tokens ?? "-"}</td><td>{fmtTime(a.started)}</td></tr>)}</tbody></table>}
      {task.evaluation?.checks && task.evaluation.checks.length > 0 && <>
        <h4>Evaluation ({task.evaluation.method})</h4>
        <ul className="small">{task.evaluation.checks.map((c, i) => <li key={i}>{c.passed ? "✓" : c.blocking ? "✗" : "!"} {c.name}{c.detail ? `: ${c.detail}` : ""}</li>)}</ul></>}
      {task.evaluation?.verification && <p className="small"><b>Build/test verification:</b> {task.evaluation.verification.status}{task.evaluation.verification.reason ? ` (${task.evaluation.verification.reason})` : ""}</p>}
      <details><summary>Generated prompt</summary>
        <div className="row"><Copy text={`${task.prompt_system}\n\n---\n\n${task.prompt_user}`} /></div>
        <pre className="pre">{task.prompt_system}{"\n\n---\n\n"}{task.prompt_user}</pre></details>
      {task.result_text && <details open><summary>Result</summary><pre className="pre">{task.result_text}</pre></details>}
    </Card>
  );
}

export default function Workflow({ project, onChange }: { project: Project; onChange: (p: Project) => void }) {
  const plan = project.plan;
  const [sel, setSel] = useState<string | null>(null);
  if (!plan) return <Banner kind="warn">No plan yet.</Banner>;
  const layers = computeLayers(plan.tasks);
  const pos = new Map<string, { x: number; y: number }>();
  layers.forEach((ids, li) => ids.forEach((id, i) => pos.set(id, { x: li * (W + GX), y: i * (H + GY) })));
  const width = layers.length * (W + GX) - GX, height = Math.max(...layers.map((l) => l.length)) * (H + GY) - GY;
  const selected = plan.tasks.find((t) => t.id === (sel ?? "")) ?? plan.tasks.find((t) => ["needs_approval", "failed", "running"].includes(t.status)) ?? plan.tasks[0];
  return (
    <div className="stack">
      <div className="legend small muted">Each column can run in parallel; arrows show which outputs feed later tasks. Click a task for prompts, results and retries.</div>
      <div className="canvas-wrap"><div className="canvas" style={{ width, height }}>
        <svg width={width} height={height} className="edges" aria-hidden>
          <defs><marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" className="arrowhead" /></marker></defs>
          {plan.tasks.flatMap((t) => t.depends_on.map((d) => {
            const a = pos.get(d), b = pos.get(t.id); if (!a || !b) return null;
            const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2, mx = (x1 + x2) / 2;
            return <path key={`${d}-${t.id}`} d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2 - 2},${y2}`} className="edge" markerEnd="url(#arr)" />;
          }))}
        </svg>
        {plan.tasks.map((t) => { const p = pos.get(t.id)!; return (
          <button key={t.id} className={`node n-${t.status} ${selected?.id === t.id ? "sel" : ""}`} style={{ left: p.x, top: p.y, width: W, height: H }} onClick={() => setSel(t.id)}>
            <div className="node-t">{t.title}</div>
            <div className="row between"><Badge status={t.status} />{t.simulated && <Sim />}</div>
            <div className="small muted">{t.handoff ? "manual handoff" : t.provider} · {t.kind}{t.attempts.length > 1 ? ` · ${t.attempts.length} tries` : ""}</div>
          </button>); })}
      </div></div>
      {selected && <Detail key={selected.id + selected.status + selected.attempts.length} project={project} task={selected} onChange={onChange} />}
    </div>
  );
}
