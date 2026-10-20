import type { Project, ProjectSummary, Provider, Settings } from "./types";

// The access token (only needed if the backend sets APP_ACCESS_TOKEN) is kept in sessionStorage.
// Provider API keys are NEVER stored in the browser: they are sent once to the backend and held there in memory.
const tokenKey = "orchestra.token";
export const getToken = () => sessionStorage.getItem(tokenKey) || "";
export const setToken = (t: string) => sessionStorage.setItem(tokenKey, t);

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (getToken()) headers["X-Access-Token"] = getToken();
  const res = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* ignore */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

/** URL for downloads / iframe previews (adds the token as a query param when one is configured). */
export const fileUrl = (path: string) => `/api${path}${getToken() ? `?token=${encodeURIComponent(getToken())}` : ""}`;

export const api = {
  health: () => req<{ ok: boolean; command_execution_enabled: boolean; auth_required: boolean }>("GET", "/health"),
  providers: () => req<Provider[]>("GET", "/providers"),
  setKey: (id: string, api_key: string) => req<Provider>("PUT", `/providers/${id}/credentials`, { api_key }),
  clearKey: (id: string) => req<Provider>("DELETE", `/providers/${id}/credentials`),
  checkProvider: (id: string) => req<Provider>("POST", `/providers/${id}/check`),
  settings: () => req<Settings>("GET", "/settings"),
  saveSettings: (patch: Partial<Settings>) => req<Settings>("PUT", "/settings", patch),
  projects: () => req<ProjectSummary[]>("GET", "/projects"),
  createProject: (b: unknown) => req<Project>("POST", "/projects", b),
  project: (id: string) => req<Project>("GET", `/projects/${id}`),
  deleteProject: (id: string) => req<{ ok: boolean }>("DELETE", `/projects/${id}`),
  upload: (id: string, filename: string, content_base64: string) => req<Project>("POST", `/projects/${id}/uploads`, { filename, content_base64 }),
  plan: (id: string) => req<Project>("POST", `/projects/${id}/plan`),
  approvePlan: (id: string) => req<Project>("POST", `/projects/${id}/plan/approve`),
  editTask: (id: string, tid: string, patch: unknown) => req<Project>("PUT", `/projects/${id}/tasks/${tid}`, patch),
  run: (id: string) => req<Project>("POST", `/projects/${id}/run`),
  cancel: (id: string) => req<Project>("POST", `/projects/${id}/cancel`),
  approveTask: (id: string, tid: string) => req<Project>("POST", `/projects/${id}/tasks/${tid}/approve`),
  skipTask: (id: string, tid: string) => req<Project>("POST", `/projects/${id}/tasks/${tid}/skip`),
  retryTask: (id: string, tid: string) => req<Project>("POST", `/projects/${id}/tasks/${tid}/retry`),
  handoff: (id: string, tid: string) => req<{ text: string; providers: Record<string, string>; note: string }>("GET", `/projects/${id}/tasks/${tid}/handoff`),
  manualResult: (id: string, tid: string, text: string) => req<Project>("POST", `/projects/${id}/tasks/${tid}/manual-result`, { text }),
};
