import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, getToken, setToken } from "./api";
import type { Project, ProjectSummary, Provider, Settings } from "./types";
import { Badge, Banner, Card } from "./ui";
import TaskComposer from "./components/TaskComposer";
import ProviderPanel from "./components/ProviderPanel";
import PlanView from "./components/PlanView";
import Workflow from "./components/Workflow";
import SettingsPage from "./components/SettingsPage";
import { Activity, Deliverables, Results } from "./components/Workspace";

type View = { name: "home" } | { name: "providers" } | { name: "settings" } | { name: "project"; id: string };
type Tab = "plan" | "workflow" | "results" | "deliverables" | "activity";

function ProjectView({ id, providers, reloadList }: { id: string; providers: Provider[]; reloadList: () => void }) {
  const [project, setProject] = useState<Project | null>(null);
  const [tab, setTab] = useState<Tab>("plan");
  const [error, setError] = useState("");
  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    try { const p = await api.project(id); setProject(p); return p; } catch (e) { setError((e as Error).message); }
  }, [id]);
  useEffect(() => { setProject(null); refresh(); }, [refresh]);
  useEffect(() => {          // poll while something is happening on the server
    window.clearInterval(timer.current);
    if (project && (project.running || project.status === "running" || project.status === "planning")) {
      timer.current = window.setInterval(async () => { const p = await refresh(); if (p && !p.running) reloadList(); }, 1000);
    }
    return () => window.clearInterval(timer.current);
  }, [project?.running, project?.status, refresh, reloadList]);

  const act = async (fn: () => Promise<Project>) => { setError(""); try { setProject(await fn()); reloadList(); } catch (e) { setError((e as Error).message); } };
  if (!project) return <p>{error || "Loading..."}</p>;
  const plan = project.plan;
  const needsApproval = plan && !plan.approved && project.execution === "manual";
  const pending = plan?.tasks.filter((t) => t.status === "needs_approval").length ?? 0;
  const tabs: [Tab, string][] = [["plan", "Plan"], ["workflow", "Workflow"], ["results", "Results"], ["deliverables", "Deliverables"], ["activity", "Activity & usage"]];

  return (
    <div className="stack">
      <div className="row between wrap">
        <div><h2>{project.name}</h2>
          <div className="row"><Badge status={project.status} /><span className={`badge ${project.mode === "demo" ? "sim" : "s-running"}`}>{project.mode === "demo" ? "DEMO (simulated)" : "INTEGRATION"}</span>
            <span className="muted small">{project.execution === "manual" ? "manual approval" : "automatic"}</span></div></div>
        <div className="row wrap">
          {needsApproval && <button className="btn primary" onClick={() => act(() => api.approvePlan(id))}>Approve plan</button>}
          {plan && !project.running && !needsApproval && !["completed", "cancelled"].includes(project.status) && (
            <button className="btn primary" onClick={() => { setTab("workflow"); act(() => api.run(id)); }}>{project.status === "planned" ? "Run workflow" : "Resume run"}{pending ? ` (${pending} awaiting approval)` : ""}</button>)}
          {project.running && <button className="btn danger" onClick={() => act(() => api.cancel(id))}>Cancel</button>}
        </div>
      </div>
      <p className="muted request">{project.request}</p>
      {error && <Banner kind="error">{error}</Banner>}
      {project.status === "paused" && <Banner kind="warn">Paused: some tasks need your approval or a manual handoff. Open the Workflow tab, handle them, then resume.</Banner>}
      {project.status === "budget_stopped" && <Banner kind="error">Stopped: the configured spending / token / call limit was reached. Remaining tasks were not run.</Banner>}
      {project.status === "failed" && <Banner kind="error">Some tasks failed. Open the Workflow tab to see errors and retry history; failed tasks can be retried within your retry limit.</Banner>}
      <div className="tabs" role="tablist">{tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>
      {tab === "plan" && <PlanView project={project} providers={providers} onChange={setProject} />}
      {tab === "workflow" && <Workflow project={project} onChange={(p) => { setProject(p); reloadList(); }} />}
      {tab === "results" && <Results project={project} />}
      {tab === "deliverables" && <Deliverables project={project} />}
      {tab === "activity" && <Activity project={project} />}
    </div>
  );
}

export default function App() {
  const [view, setView] = useState<View>({ name: "home" });
  const [providers, setProviders] = useState<Provider[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [authNeeded, setAuthNeeded] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [offline, setOffline] = useState(false);

  const reloadProviders = useCallback(async () => { try { setProviders(await api.providers()); setSettings(await api.settings()); setOffline(false); setAuthNeeded(false); } catch (e) {
    if (e instanceof ApiError && e.status === 401) setAuthNeeded(true); else setOffline(true); } }, []);
  const reloadList = useCallback(async () => { try { setProjects(await api.projects()); } catch { /* shown via offline banner */ } }, []);
  useEffect(() => { reloadProviders(); reloadList(); }, [reloadProviders, reloadList]);

  if (authNeeded) return (
    <div className="center"><Card title="Access token required">
      <p className="muted">This server was started with APP_ACCESS_TOKEN set.</p>
      <input type="password" value={tokenInput} onChange={(e) => setTokenInput(e.target.value)} placeholder="token" />
      <div className="row end"><button className="btn primary" onClick={() => { setToken(tokenInput); reloadProviders(); reloadList(); }}>Continue</button></div>
      {getToken() && <Banner kind="error">Token rejected.</Banner>}</Card></div>);

  const nav = (name: View["name"], label: string) => <button className={view.name === name ? "on" : ""} onClick={() => setView({ name } as View)}>{label}</button>;
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">◈ One AI</div>
        <div className="muted small">Universal AI Orchestration Platform</div>
        <nav>{nav("home", "New task")}{nav("providers", "Providers")}{nav("settings", "Settings")}</nav>
        <h4>Projects</h4>
        <ul className="plist">{projects.map((p) => (
          <li key={p.id}><button className={view.name === "project" && (view as { id: string }).id === p.id ? "on" : ""} onClick={() => setView({ name: "project", id: p.id })}>
            <span className="pname">{p.name}</span><Badge status={p.status} /></button></li>))}
          {projects.length === 0 && <li className="muted small">No projects yet</li>}</ul>
        <div className="provstat">{providers.map((p) => <div key={p.id} className="small"><span className={`dot ${p.id === "demo" || p.configured ? "ok" : ""}`} /> {p.display_name}</div>)}</div>
      </aside>
      <main className="main">
        {offline && <Banner kind="error">Cannot reach the backend. Start it with <code>uvicorn app.main:app</code> in <code>backend/</code> (see README).</Banner>}
        {view.name === "home" && (<div className="stack">
          <TaskComposer providers={providers} onCreated={(id) => { reloadList(); setView({ name: "project", id }); }} />
          <p className="muted small"><b>One Interface. Multiple AI Capabilities. One Coordinated Workflow.</b> One AI plans your request into a task graph, writes a tailored prompt for each step, routes steps to the providers you've configured, passes results between steps, checks them against acceptance criteria, and stops at the limits you set.</p>
        </div>)}
        {view.name === "providers" && <ProviderPanel providers={providers} settings={settings} reload={reloadProviders} />}
        {view.name === "settings" && <SettingsPage settings={settings} providers={providers} reload={reloadProviders} />}
        {view.name === "project" && <ProjectView key={view.id} id={view.id} providers={providers} reloadList={reloadList} />}
      </main>
    </div>
  );
}
