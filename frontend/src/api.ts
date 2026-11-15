// Shapes of what the Python API returns (see src/release_impact_brief/views.py and panels.py).

export type Confidence = "exact" | "likely" | "guess" | "unknown";

export interface Evidence { text: string; file: string | null; line: number | null }
export interface Verdict { value: string; evidence: Evidence[] }

export interface FactInfo {
  type: "node" | "edge";
  kind: string;
  name?: string;
  src?: string;
  dst?: string;
  confidence?: Confidence;
  file: string;
  line: number;
}

export interface DiagramEdge {
  from: string;
  to: string;
  label: string;
  ref: string | null;
  inferred: boolean;
  reason?: string;
  confidence: Confidence | null;
}

export interface ArchitectureSection {
  type: "architecture_diagram";
  title: string;
  groups: { label: string; style: "solid" | "dashed"; members: string[] }[];
  edges: DiagramEdge[];
  inferred: { label: string; reason: string; evidence: string[] }[];
}
export interface FlowSection { type: "flow"; title: string; description: string; steps: string[] }
export interface NarrativeSection { type: "narrative"; title: string; body: string }
export type Section = ArchitectureSection | FlowSection | NarrativeSection;

export interface Located { file: string; line: number }
export interface Usage extends Located { service: string; kind: string; confidence: Confidence }

export interface Panels {
  services: (Located & { id: string; name: string; module: string | null; port: string | null;
    roles: string[]; deployed_by: string[]; summary: string | null })[];
  modules: (Located & { id: string; name: string; build_tools: string[]; summary: string | null;
    capabilities: string[]; depends_on: string[]; service: string | null;
    classes: { id: string; name: string; role: string | null; summary: string | null;
      error: string | null; file: string | null; line: number | null }[] })[];
  endpoints: (Located & { id: string; method: string; path: string; path_resolved: boolean;
    handler: string; service: string | null; returns: string; response: string; branches: number;
    inputs: { name: string; type: string; source: string; implicit?: boolean;
      required?: boolean; default?: string; validated?: boolean }[] })[];
  entities: (Located & { id: string; table: string; class: string; table_source: string;
    relations: (Located & { field: string; kind: string; target: string;
      confidence: Confidence })[];
    repositories: string[] })[];
  data_stores: (Located & { id: string; name: string;
    locations: { value: string; file: string; line: number }[]; used_by: Usage[] })[];
  external_systems: (Located & { id: string; name: string;
    locations: { value: string; file: string; line: number }[]; used_by: Usage[] })[];
  unclear: { kind: string; subject: string; text: string; file: string | null;
    line: number | null }[];
}

export interface View {
  repo: string;
  commit: string;
  model: string;
  repo_layout: Verdict;
  runtime_style: Verdict;
  summary?: { summary: string; organization: string };
  sections: Section[];
  facts: Record<string, FactInfo>;
  removed: { where: string; ref: unknown; reason: string }[];
  panels: Panels;
}

export interface ViewListing {
  name: string; repo: string; commit: string; model: string; runtime: string; layout: string;
}

export interface Job {
  id: string;
  url: string;
  ref: string | null;
  status: "queued" | "fetching" | "extracting" | "awaiting_confirmation" | "summarizing"
    | "viewing" | "done" | "failed" | "cancelled";
  total: number;
  completed: number;
  view: string | null;
  estimate: { min_usd: number; max_usd: number; cap_usd: number } | null;
  error: string | null;
}

export interface Branch {
  kind: "if" | "switch" | "ternary" | "catch";
  line: number;
  end_line: number;
  parent: number | null;
  condition: string;
  exception?: string;
  drivers: string[];
  exits: number[];
  else_exits?: number[];
  cases?: { label: string; line: number; exits: number[] }[];
  calls: string[];
}

export interface InputRow {
  name: string;
  type: string;
  source: string;
  field: string | null;
  constraints?: string[];
  role: "drives" | "read as a whole" | "passed through" | "unused";
  lines: number[];
  file?: string;
  line?: number;
  implicit?: boolean;
  validated?: boolean;
}

export interface BranchMap {
  endpoint: string;
  name: string;
  handler: string;
  file: string;
  line: number;
  end_line: number;
  inputs: InputRow[];
  branches: Branch[];
  returns: number[];
  throws: number[];
  validation: Record<string, string[]>;
  custom_validator: string | null;
  library_calls: string[];
  unresolved_calls: string[];
}

export interface ModuleFlows {
  module: string;
  flows: { type: "flow"; title: string; description: string; steps: string[] }[];
  removed: { where: string; ref: unknown; reason: string }[];
  facts: Record<string, FactInfo>;
}

export interface NeedsConfirmation {
  needs_confirmation: true;
  estimate: { min_usd: number; max_usd: number; cap_usd: number };
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json" },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
  }
  return body as T;
}

export const api = {
  start: (url: string, ref: string) =>
    call<Job>("/api/analyses", { method: "POST", body: JSON.stringify({ url, ref: ref || null }) }),
  job: (id: string) => call<Job>(`/api/analyses/${encodeURIComponent(id)}`),
  confirm: (id: string) => call<Job>(`/api/analyses/${encodeURIComponent(id)}/confirm`, { method: "POST" }),
  cancel: (id: string) => call<Job>(`/api/analyses/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  views: () => call<ViewListing[]>("/api/views"),
  view: (name: string) => call<View>(`/api/views/${encodeURIComponent(name)}`),
  branches: (name: string, endpoint: string) =>
    call<BranchMap>(`/api/views/${encodeURIComponent(name)}/branches?endpoint=${encodeURIComponent(endpoint)}`),
  flows: (name: string, module: string, confirm = false) =>
    call<ModuleFlows | NeedsConfirmation>(`/api/views/${encodeURIComponent(name)}/flows`,
      { method: "POST", body: JSON.stringify({ module, confirm }) }),
};

/** Link to a line of the analysed commit on GitHub. */
export function sourceUrl(repo: string, commit: string, file: string, line?: number | null): string {
  const path = file.split("/").map(encodeURIComponent).join("/");
  return `${repo}/blob/${commit}/${path}${line ? `#L${line}` : ""}`;
}

/** Hash routes: a view, one endpoint's branch map, one module. */
export const routes = {
  view: (name: string) => `#/view/${encodeURIComponent(name)}`,
  endpoint: (name: string, id: string) =>
    `#/view/${encodeURIComponent(name)}/endpoint/${encodeURIComponent(id)}`,
  module: (name: string, id: string) =>
    `#/view/${encodeURIComponent(name)}/module/${encodeURIComponent(id)}`,
};

/** The innermost module (`build:module:<dir>`) whose folder holds `file`. */
export function moduleOf(file: string, modules: string[]): string | null {
  let best: string | null = null;
  let bestLength = -1;
  for (const id of modules) {
    const dir = id.replace(/^build:module:/, "");
    const length = dir === "." ? 0 : dir.length;
    if ((dir === "." || file.startsWith(`${dir}/`)) && length > bestLength) {
      best = id;
      bestLength = length;
    }
  }
  return best;
}

/** "fact:service:web" -> "service:web" */
export const factId = (ref: string) => ref.replace(/^fact:/, "");
