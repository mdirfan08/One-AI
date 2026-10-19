import { useEffect, useState } from "react";
import { api } from "../api";
import type { Provider, Settings } from "../types";
import { Banner, Card } from "../ui";

const KINDS = ["research", "writing", "document", "presentation", "code_spec", "code_files", "code_review", "image", "social"];

export default function SettingsPage({ settings, providers, reload }: { settings: Settings | null; providers: Provider[]; reload: () => void }) {
  const [s, setS] = useState<Settings | null>(settings);
  const [msg, setMsg] = useState("");
  useEffect(() => setS(settings), [settings]);
  if (!s) return <p>Loading...</p>;
  const real = providers.filter((p) => p.id !== "demo");
  const save = async () => {
    try {
      await api.saveSettings({ provider_order: s.provider_order, provider_preferences: s.provider_preferences, planner_provider: s.planner_provider,
        llm_evaluation: s.llm_evaluation, default_limits: s.default_limits, privacy: s.privacy, default_mode: s.default_mode });
      setMsg("Saved."); reload();
    } catch (e) { setMsg((e as Error).message); }
  };
  const lim = (k: string, v: string) => setS({ ...s, default_limits: { ...s.default_limits, [k]: v === "" ? null : Number(v) } });
  return (
    <div className="stack">
      <h2>Settings</h2>
      <Card title="Routing">
        <label>Provider preference order (first eligible wins; blank = automatic by capability, then lowest known price)</label>
        <div className="row wrap">{[0, 1, 2].map((i) => (
          <select key={i} value={s.provider_order[i] ?? ""} onChange={(e) => { const o = [...s.provider_order]; o[i] = e.target.value; setS({ ...s, provider_order: o.filter(Boolean) }); }}>
            <option value="">-</option>{real.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}</select>))}</div>
        <label>Planner provider</label>
        <select value={s.planner_provider ?? ""} onChange={(e) => setS({ ...s, planner_provider: e.target.value || null })}>
          <option value="">Automatic</option>{real.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}</select>
        <label>Per-task-type overrides</label>
        <div className="grid3">{KINDS.map((k) => (
          <div key={k}><span className="small">{k}</span>
            <select value={s.provider_preferences[k] ?? ""} onChange={(e) => { const p = { ...s.provider_preferences }; if (e.target.value) p[k] = e.target.value; else delete p[k]; setS({ ...s, provider_preferences: p }); }}>
              <option value="">Auto</option>{real.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}</select></div>))}</div>
        <p className="muted small">No provider is assumed to be best at anything: preferences are yours. Unsupported capabilities (e.g. Anthropic image generation) are always skipped.</p>
      </Card>
      <Card title="Execution defaults and limits">
        <div className="grid3">
          {([["max_tasks", "Max tasks"], ["max_retries", "Max retries per task"], ["max_parallel", "Max parallel tasks"], ["max_duration_s", "Max run seconds"],
             ["max_calls", "Max provider calls"], ["max_total_tokens", "Max tokens"], ["max_usd", "Spending limit USD (blank = none)"]] as const).map(([k, label]) => (
            <div key={k}><label>{label}</label><input type="number" value={s.default_limits[k] ?? ""} onChange={(e) => lim(k, e.target.value)} /></div>))}
        </div>
        <label className="check"><input type="checkbox" checked={s.llm_evaluation} onChange={(e) => setS({ ...s, llm_evaluation: e.target.checked })} /> Judge each result against its acceptance criteria with an extra model call (costs tokens; structural checks always run)</label>
        <label>Default mode</label>
        <select value={s.default_mode} onChange={(e) => setS({ ...s, default_mode: e.target.value as "demo" | "integration" })}><option value="demo">Demo</option><option value="integration">Integration</option></select>
      </Card>
      <Card title="Privacy">
        <label className="check"><input type="checkbox" checked={s.privacy.send_attachments_to_providers} onChange={(e) => setS({ ...s, privacy: { ...s.privacy, send_attachments_to_providers: e.target.checked } })} /> Include attached files in prompts sent to providers</label>
        <label className="check"><input type="checkbox" checked={s.privacy.include_prompts_in_export} onChange={(e) => setS({ ...s, privacy: { ...s.privacy, include_prompts_in_export: e.target.checked } })} /> Include prompts in exported project data</label>
        <Banner>Your requests, attachments and earlier outputs are sent to whichever provider handles each task, and are processed under that provider's own terms and retention policies. Projects, prompts and results are stored <b>unencrypted</b> in a local SQLite file and data folder. API keys are never stored on disk.</Banner>
      </Card>
      <div className="row end">{msg && <span className="small">{msg}</span>}<button className="btn primary" onClick={save}>Save settings</button></div>
    </div>
  );
}
