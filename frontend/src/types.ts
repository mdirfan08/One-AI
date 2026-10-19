export type TaskStatus = "planned" | "waiting" | "running" | "needs_approval" | "completed" | "failed" | "blocked";

export interface Attempt {
  n: number; provider: string; model: string | null; ok: boolean | null; error: string | null; error_code: string | null;
  simulated: boolean; started: number; finished?: number; input_tokens?: number | null; output_tokens?: number | null;
}
export interface Check { name: string; passed: boolean; blocking: boolean; detail: string }
export interface Task {
  id: string; title: string; kind: string; description: string; depends_on: string[]; inputs: string[];
  expected_outputs: { name: string; type: string }[]; acceptance_criteria: string[]; preferred_provider: string | null;
  requires_approval: boolean; status: TaskStatus; prompt_system: string; prompt_user: string; prompt_edited: boolean;
  provider: string | null; model: string | null; routing_reason: string; result_text: string; artifact_ids: string[];
  attempts: Attempt[]; error: string | null; error_code: string | null; handoff: boolean; simulated: boolean;
  estimated_cost_usd: number | null; manual_retries: number;
  evaluation: { passed?: boolean | null; method?: string; checks?: Check[]; verification?: any };
}
export interface Plan {
  objective: string; requirements: string[]; assumptions: string[]; clarifying_questions: string[]; deliverables: string[];
  tasks: Task[]; approved: boolean; planner_provider: string | null; simulated: boolean;
  estimated_cost_usd: number | null; cost_note: string;
}
export interface Artifact {
  id: string; name: string; type: string; task_id: string | null; mime: string; size: number; simulated: boolean;
  source: string; provider: string | null; meta: Record<string, any>;
}
export interface Usage { calls: number; input_tokens: number; output_tokens: number; usd: number; unpriced_calls: number; simulated_calls: number }
export interface Project {
  id: string; name: string; request: string; mode: "demo" | "integration"; execution: "auto" | "manual"; status: string;
  constraints: string; limits: Record<string, number | null>; plan: Plan | null; artifacts: Artifact[]; usage: Usage;
  events: { ts: number; level: string; message: string; task_id: string | null }[];
  final: null | { status: string; summary_md: string; deliverables: any[]; not_produced: any[]; simulated: boolean; caveats: string[] };
  running: boolean; workspace_files: { path: string; bytes: number }[]; command_execution_enabled: boolean;
  attachments: { name: string; size: number }[];
}
export interface ProjectSummary { id: string; name: string; status: string; mode: string; updated_at: number }
export interface Provider {
  id: string; display_name: string; configured: boolean; credential_source: string | null; model: string; image_model: string;
  capabilities: string[]; connection: string; last_request_ok: boolean | null; last_request_at: number | null;
  last_error: string | null; last_error_code: string | null; quota_info: string; may_charge: boolean; notes: string[];
  pricing_known?: boolean;
}
export interface Settings {
  models: Record<string, { model: string; image_model: string }>; provider_order: string[];
  provider_preferences: Record<string, string>; planner_provider: string | null; llm_evaluation: boolean;
  default_limits: Record<string, number | null>; privacy: { send_attachments_to_providers: boolean; include_prompts_in_export: boolean };
  default_mode: "demo" | "integration";
}
