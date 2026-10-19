import { useState } from "react";
import { api } from "../api";
import type { Provider, Settings } from "../types";
import { Badge, Banner, Card, fmtTime } from "../ui";

const LINKS: Record<string, string> = { openai: "https://platform.openai.com/api-keys", anthropic: "https://console.anthropic.com/settings/keys", gemini: "https://aistudio.google.com/apikey" };

function ProviderCard({ p, settings, reload }: { p: Provider; settings: Settings | null; reload: () => void }) {
  const [key, setKey] = useState("");
  const [model, setModel] = useState(p.model);
  const [imageModel, setImageModel] = useState(p.image_model);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>, ok = "") => {
    setBusy(true); setMsg("");
    try { await fn(); setMsg(ok); reload(); } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  };
  const demo = p.id === "demo";
  const connLabel = demo ? "simulated" : p.connection === "unknown" && p.configured ? "key present, not tested" : p.connection.replace("_", " ");
  return (
    <Card title={<>{p.display_name} <Badge status={p.connection === "ok" ? "completed" : p.connection === "error" ? "failed" : "planned"}>{connLabel}</Badge></>}>
      <div className="kv"><span>Credentials</span><b>{demo ? "not needed" : p.configured ? `present (${p.credential_source})` : "missing"}</b></div>
      <div className="kv"><span>Model</span><b>{p.model}</b></div>
      {!demo && p.id !== "anthropic" && <div className="kv"><span>Image model</span><b>{p.image_model || "none (image generation off)"}</b></div>}
      <div className="kv"><span>Capabilities</span><span className="chips">{p.capabilities.map((c) => <span className="chip" key={c}>{c.replace("_", " ")}</span>)}</span></div>
      <div className="kv"><span>Last request</span><b>{p.last_request_ok == null ? "none yet" : `${p.last_request_ok ? "OK" : "failed"} at ${fmtTime(p.last_request_at)}`}</b></div>
      {p.last_error && <Banner kind="error">{p.last_error_code}: {p.last_error}</Banner>}
      <div className="kv"><span>Quota</span><span className="muted small">{p.quota_info}</span></div>
      {!demo && <div className="kv"><span>Pricing</span><span className="muted small">{p.pricing_known ? "configured in pricing file" : "unknown (add to pricing file for USD estimates/limits)"}</span></div>}
      {p.notes.map((n) => <p key={n} className="muted small">{n}</p>)}
      {!demo && (<>
        <label>API key (session only)</label>
        <div className="row">
          <input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={p.configured ? "replace key..." : "paste key"} />
          <button className="btn" disabled={busy || key.length < 8} onClick={() => run(async () => { await api.setKey(p.id, key); setKey(""); }, "Key held in backend memory.")}>Use key</button>
          {p.credential_source === "session" && <button className="btn" disabled={busy} onClick={() => run(() => api.clearKey(p.id), "Key cleared.")}>Clear</button>}
        </div>
        <p className="muted small">Stored only in the backend process memory: not written to disk, the database, exports or logs, and not encrypted at rest. It is lost on restart. It travels from your browser to your own backend, so use it over localhost or HTTPS only. <a href={LINKS[p.id]} target="_blank" rel="noreferrer">Get a key</a>. Alternatively set it in the backend environment.</p>
        <div className="grid2">
          <div><label>Text model</label><input value={model} onChange={(e) => setModel(e.target.value)} /></div>
          {p.id !== "anthropic" && <div><label>Image model (blank = off)</label><input value={imageModel} onChange={(e) => setImageModel(e.target.value)} /></div>}
        </div>
        <div className="row">
          <button className="btn" disabled={busy || !settings} onClick={() => run(() => api.saveSettings({ models: { ...settings!.models, [p.id]: { model, image_model: p.id === "anthropic" ? "" : imageModel } } }), "Models saved.")}>Save models</button>
          <button className="btn" disabled={busy || !p.configured} onClick={() => run(() => api.checkProvider(p.id), "Checked.")}>Test connection</button>
        </div>
        <p className="muted small">"Test connection" lists models; it does not generate text. Default model names are suggestions: verify them in the provider's docs.</p>
        {p.may_charge && <Banner kind="warn">API usage may be billed by {p.display_name}. Check their pricing and free-tier limits.</Banner>}
      </>)}
      {msg && <p className="small">{msg}</p>}
    </Card>
  );
}

export default function ProviderPanel({ providers, settings, reload }: { providers: Provider[]; settings: Settings | null; reload: () => void }) {
  return (
    <div className="stack">
      <h2>Providers</h2>
      <p className="muted">Connect any of the three official APIs, or use the free demo provider. The router chooses per task from providers that are configured and support the task; you can also pin a provider on any task.</p>
      <div className="grid-cards">{providers.map((p) => <ProviderCard key={p.id + p.model + p.image_model} p={p} settings={settings} reload={reload} />)}</div>
    </div>
  );
}
