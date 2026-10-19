import { useState } from "react";
import { api } from "../api";
import type { Project, Provider, Task } from "../types";
import { Banner, Card } from "../ui";
import { money } from "../lib/graph";

function TaskEditor({ project, task, providers, onChange, locked }: { project: Project; task: Task; providers: Provider[]; onChange: (p: Project) => void; locked: boolean }) {
  const [sys, setSys] = useState(task.prompt_system);
  const [usr, setUsr] = useState(task.prompt_user);
  const [crit, setCrit] = useState(task.acceptance_criteria.join("\n"));
  const [pref, setPref] = useState(task.preferred_provider ?? "");
  const [appr, setAppr] = useState(task.requires_approval);
  const [err, setErr] = useState("");
  const save = async () => {
    setErr("");
    try {
      onChange(await api.editTask(project.id, task.id, {
        prompt_system: sys, prompt_user: usr, acceptance_criteria: crit.split("\n").map((s) => s.trim()).filter(Boolean),
        preferred_provider: pref || null, requires_approval: appr,
      }));
    } catch (e) { setErr((e as Error).message); }
  };
  const eligible = providers.filter((p) => (project.mode === "demo" ? p.id === "demo" : p.id !== "demo"));
  return (
    <div className="stack">
      <div className="grid2">
        <div><label>Provider</label>
          <select value={pref} disabled={locked} onChange={(e) => setPref(e.target.value)}>
            <option value="">Automatic (router)</option>
            {eligible.map((p) => <option key={p.id} value={p.id}>{p.display_name}{p.configured ? "" : " (no key)"}</option>)}
          </select></div>
        <label className="check"><input type="checkbox" disabled={locked} checked={appr} onChange={(e) => setAppr(e.target.checked)} /> Require my approval before this runs</label>
      </div>
      <label>Acceptance criteria (one per line)</label>
      <textarea rows={3} disabled={locked} value={crit} onChange={(e) => setCrit(e.target.value)} />
      <label>System prompt (editable)</label>
      <textarea rows={4} disabled={locked} value={sys} onChange={(e) => setSys(e.target.value)} />
      <label>Task prompt (editable). Outputs of earlier stages are appended automatically at run time</label>
      <textarea rows={12} disabled={locked} value={usr} onChange={(e) => setUsr(e.target.value)} className="mono" />
      {err && <Banner kind="error">{err}</Banner>}
      {!locked && <div className="row end"><button className="btn" onClick={save}>Save changes{task.prompt_edited ? " (edited)" : ""}</button></div>}
    </div>
  );
}

export default function PlanView({ project, providers, onChange }: { project: Project; providers: Provider[]; onChange: (p: Project) => void }) {
  const plan = project.plan;
  const [open, setOpen] = useState<string | null>(null);
  if (!plan) return <Banner kind="warn">No plan yet. Planning may have failed: check the activity log, then try again from a new project.</Banner>;
  const locked = project.running || plan.tasks.some((t) => t.status === "completed" || t.status === "running");
  return (
    <div className="stack">
      {plan.simulated && <Banner>This plan came from the demo planner (a keyword heuristic), not an AI model.</Banner>}
      {plan.clarifying_questions.length > 0 && (
        <Banner kind="warn"><b>The planner has questions.</b> You can proceed with the stated assumptions, or start a new project with more detail.
          <ul>{plan.clarifying_questions.map((q) => <li key={q}>{q}</li>)}</ul></Banner>)}
      <Card title="Objective"><p>{plan.objective}</p>
        <div className="grid2">
          <div><h4>Requirements</h4><ul>{plan.requirements.map((r) => <li key={r}>{r}</li>)}</ul></div>
          <div><h4>Assumptions (visible defaults)</h4><ul>{plan.assumptions.map((r) => <li key={r}>{r}</li>)}</ul></div>
        </div>
        <h4>Expected deliverables</h4><ul>{plan.deliverables.map((r) => <li key={r}>{r}</li>)}</ul>
        <p className="small"><b>Estimated cost:</b> {project.mode === "demo" ? "$0.00 (simulated)" : money(plan.estimated_cost_usd)} <span className="muted">{plan.cost_note}</span></p>
      </Card>
      <h3>Tasks ({plan.tasks.length})</h3>
      {plan.tasks.map((t) => (
        <Card key={t.id} className="task">
          <button className="task-h" onClick={() => setOpen(open === t.id ? null : t.id)} aria-expanded={open === t.id}>
            <div><b>{t.title}</b> <span className="chip">{t.kind}</span>
              {t.depends_on.length > 0 && <span className="muted small"> after: {t.depends_on.join(", ")}</span>}</div>
            <div className="small muted">
              {t.handoff ? "Manual handoff" : `${t.provider} / ${t.model}`} · est. {project.mode === "demo" ? "$0" : money(t.estimated_cost_usd)}
            </div>
          </button>
          <p className="small">{t.description}</p>
          <p className="small muted">Why this provider: {t.routing_reason}</p>
          <p className="small"><b>Outputs:</b> {t.expected_outputs.map((o) => `${o.name} (${o.type})`).join(", ")} · <b>Criteria:</b> {t.acceptance_criteria.join("; ")}</p>
          {t.requires_approval && <span className="badge s-needs_approval">Approval required</span>}
          {open === t.id && <TaskEditor project={project} task={t} providers={providers} onChange={onChange} locked={locked} />}
        </Card>
      ))}
    </div>
  );
}
