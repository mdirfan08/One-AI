import { fileUrl } from "../api";
import type { Project } from "../types";
import { Badge, Banner, Card, Sim } from "../ui";

export function Results({ project }: { project: Project }) {
  const tasks = project.plan?.tasks.filter((t) => t.result_text) ?? [];
  if (!tasks.length) return <p className="muted">No results yet.</p>;
  return <div className="stack">{tasks.map((t) => (
    <Card key={t.id} title={<>{t.title} <Badge status={t.status} /> {t.simulated && <Sim />}</>}>
      <p className="small muted">{t.provider}{t.model ? ` / ${t.model}` : ""} · {t.kind}</p>
      {t.kind === "research" && <Banner kind="warn">Model-generated research: claims and any links are <b>not verified</b>.</Banner>}
      <pre className="pre">{t.result_text}</pre>
    </Card>))}</div>;
}

export function Deliverables({ project }: { project: Project }) {
  const f = project.final;
  const hasIndex = project.workspace_files.some((x) => x.path === "index.html");
  return (
    <div className="stack">
      {!f && <Banner>Deliverables appear after a run finishes or pauses.</Banner>}
      {f?.simulated && <Banner kind="warn">Some or all outputs are <b>SIMULATED</b> (demo provider). They are placeholders, not real AI output.</Banner>}
      {f?.caveats.map((c) => <Banner key={c} kind="warn">{c}</Banner>)}
      <Card title="Artifacts" actions={<>
        <a className="btn sm" href={fileUrl(`/projects/${project.id}/export`)}>Export project JSON</a>
        {project.workspace_files.length > 0 && <a className="btn sm" href={fileUrl(`/projects/${project.id}/package`)}>Download code (.zip)</a>}</>}>
        {project.artifacts.length === 0 ? <p className="muted">Nothing produced yet.</p> :
          <table className="tbl"><thead><tr><th>Name</th><th>Type</th><th>From</th><th>Origin</th><th /></tr></thead><tbody>
            {project.artifacts.map((a) => <tr key={a.id}><td>{a.name}</td><td>{a.type}</td><td>{a.task_id ?? "system"}</td>
              <td>{a.simulated ? <Sim /> : a.source === "manual" ? <span className="badge">manual paste</span> : <span className="badge s-completed">{a.provider ?? "ai"}</span>}</td>
              <td><a href={fileUrl(`/projects/${project.id}/artifacts/${a.id}`)}>Download</a></td></tr>)}
          </tbody></table>}
      </Card>
      {f && <Card title="Final summary"><pre className="pre">{f.summary_md}</pre></Card>}
      {project.workspace_files.length > 0 && <Card title="Generated project files">
        <ul className="small mono">{project.workspace_files.map((x) => <li key={x.path}>{x.path} <span className="muted">({x.bytes} B)</span></li>)}</ul>
        {hasIndex ? <>
          <p className="small muted">Static preview, sandboxed (scripts allowed, no network, isolated origin). Vite/npm projects must be built first.</p>
          <iframe title="Preview" className="preview" sandbox="allow-scripts" src={fileUrl(`/projects/${project.id}/preview/index.html`)} /></> :
          <p className="muted small">No index.html at the workspace root, so no static preview. Download the zip and run it locally.</p>}
      </Card>}
    </div>
  );
}

export function Activity({ project }: { project: Project }) {
  const u = project.usage;
  return (
    <div className="stack">
      <Card title="Usage and limits">
        <div className="kv"><span>Provider calls</span><b>{u.calls} ({u.simulated_calls} simulated) / limit {project.limits.max_calls}</b></div>
        <div className="kv"><span>Real tokens</span><b>{u.input_tokens + u.output_tokens} / limit {project.limits.max_total_tokens}</b></div>
        <div className="kv"><span>Known cost</span><b>${u.usd.toFixed(4)}{project.limits.max_usd != null ? ` / limit $${project.limits.max_usd}` : " (no USD limit)"}</b></div>
        {u.unpriced_calls > 0 && <p className="small muted">{u.unpriced_calls} call(s) had no configured price, so real spend may be higher than shown.</p>}
      </Card>
      <Card title="Activity log"><ul className="log small">{[...project.events].reverse().map((e, i) =>
        <li key={i} className={`l-${e.level}`}><span className="muted">{new Date(e.ts * 1000).toLocaleTimeString()}</span> {e.message}</li>)}</ul></Card>
      {project.attachments.length > 0 && <Card title="Attachments"><ul className="small">{project.attachments.map((a) => <li key={a.name}>{a.name} ({a.size} B)</li>)}</ul></Card>}
    </div>
  );
}

