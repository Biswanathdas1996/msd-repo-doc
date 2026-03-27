import { useState, useEffect, useCallback, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

const API_BASE = "/api/py-api";

// ─── Types ──────────────────────────────────────────────────────────────────

interface AdvancedDocSummary {
  id: string;
  name: string;
  created_at: string;
  status: string;
  file_count: number;
}

interface KGNode {
  id: string;
  name: string;
  type: string;
  description: string;
  file_path: string;
}

interface KGEdge {
  source: string;
  target: string;
  relationship: string;
}

interface FeatureComponent {
  name: string;
  type: string;
  file_path: string;
  role: string;
}

interface Feature {
  id: string;
  name: string;
  description: string;
  components: FeatureComponent[];
  entry_points: string[];
  data_flow: string;
}

interface FeatureConnection {
  source_feature: string;
  target_feature: string;
  connection_type: string;
  description: string;
  shared_components: string[];
}

interface FeatureGroup {
  group_name: string;
  feature_ids: string[];
  description: string;
}

interface FlowDiagram {
  feature_id: string;
  title: string;
  mermaid: string;
  description: string;
}

export interface QualityScore {
  overall_score: number;
  kg_score: { score: number; issues: string[]; total_nodes: number; total_edges: number };
  feature_score: { score: number; issues: string[]; total_features: number };
  has_connections: boolean;
  has_diagrams: boolean;
  has_documentation: boolean;
  meets_target: boolean;
}

export interface TechnicalSpecs {
  scope_definition?: {
    in_scope: string[];
    out_of_scope: string[];
    summary: string;
  } | null;
  solution_overview?: {
    summary: string;
    tech_stack: string[];
    deployment_model: string;
    key_capabilities: string[];
  } | null;
  high_level_architecture?: {
    description: string;
    layers: { name: string; description: string; components: string[] }[];
    mermaid_diagram?: string;
  } | null;
  erd?: {
    description: string;
    entities: {
      name: string;
      type: string;
      fields: { name: string; type: string; description: string; is_key: boolean; is_required: boolean }[];
      relationships: string[];
    }[];
    mermaid_diagram?: string;
  } | null;
  standard_and_custom_entities?: {
    standard_entities: { name: string; purpose: string; customizations: string[] }[];
    custom_entities: { name: string; purpose: string; fields_summary: string }[];
  } | null;
  business_rules?: {
    workflows: { name: string; trigger: string; description: string; steps: string[] }[];
    validation_rules: string[];
    automation: string[];
  } | null;
  javascript_customizations?: {
    client_scripts: { name: string; file_path: string; purpose: string; events_handled: string[] }[];
    web_resources: string[];
    libraries_used: string[];
  } | null;
  auth_model?: {
    authentication_method: string;
    authorization_model: string;
    roles: { name: string; permissions: string[] }[];
    security_features: string[];
    file_paths: string[];
  } | null;
  module_components?: {
    sales?: {
      components: { name: string; type: string; description: string; file_path: string }[];
      mermaid_diagram?: string;
    };
    service?: {
      components: { name: string; type: string; description: string; file_path: string }[];
      mermaid_diagram?: string;
    };
    marketing?: {
      components: { name: string; type: string; description: string; file_path: string }[];
      mermaid_diagram?: string;
    };
  } | null;
  integration_architecture?: {
    description: string;
    integrations: {
      name: string;
      type: string;
      direction: string;
      external_system: string;
      description: string;
      endpoints: string[];
      file_paths: string[];
    }[];
    mermaid_diagram?: string;
  } | null;
  integration_auth?: {
    mechanisms: {
      integration_name: string;
      auth_type: string;
      description: string;
      token_management: string;
      file_paths: string[];
    }[];
  } | null;
}

export interface AdvancedDocResult {
  id: string;
  name: string;
  created_at: string;
  status: string;
  error?: string;
  file_count: number;
  files: { path: string; size: number; lines: number }[];
  project_tree: string;
  knowledge_graph: { nodes: KGNode[]; edges: KGEdge[] };
  features: { features: Feature[] };
  feature_connections: {
    connections: FeatureConnection[];
    feature_groups: FeatureGroup[];
  };
  flow_diagrams: {
    diagrams: FlowDiagram[];
    system_overview_diagram?: FlowDiagram;
  };
  technical_specs?: TechnicalSpecs;
  documentation: string;
  quality_score?: QualityScore;
  cross_validation?: { confidence: number; assessment: string; corrections: string[] };
  completed_steps?: string[];
  current_step?: string;
  step_errors?: Record<string, string>;
  output_folder?: string;
  section_jobs?: {
    technical_specs?: "running";
    technical_specs_started_at?: string;
  };
}

// ─── SSE streaming types ────────────────────────────────────────────────────

export type StepStatus =
  | "pending"
  | "running"
  | "complete"
  | "error"
  | "skipped";

export interface StreamStep {
  step: string;
  label: string;
  step_index: number;
  status: StepStatus;
  summary?: string;
  error?: string;
}

const STEP_DEFAULTS: StreamStep[] = [
  { step: "extraction",          label: "Extracting & cleaning files",     step_index: 0, status: "pending" },
  { step: "knowledge_graph",     label: "Building knowledge graph",        step_index: 1, status: "pending" },
  { step: "features",            label: "Identifying features",            step_index: 2, status: "pending" },
  { step: "cross_validation",    label: "Cross-validating results",        step_index: 3, status: "pending" },
  { step: "feature_connections", label: "Analyzing feature connections",   step_index: 4, status: "pending" },
  { step: "flow_diagrams",       label: "Generating flow diagrams",        step_index: 5, status: "pending" },
  { step: "technical_specs",     label: "Extracting technical specifications", step_index: 6, status: "pending" },
  { step: "documentation",       label: "Writing documentation",           step_index: 7, status: "pending" },
  { step: "quality_check",       label: "Quality scoring",                 step_index: 8, status: "pending" },
];

// ─── Helpers ────────────────────────────────────────────────────────────────

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    cache: "no-store",
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

// ─── List / detail hooks (unchanged) ────────────────────────────────────────

export function useAdvancedDocsList() {
  return useQuery<AdvancedDocSummary[]>({
    queryKey: ["advanced-docs"],
    queryFn: () => fetchJson(`${API_BASE}/advanced-docs`),
    refetchInterval: 5000,
  });
}

export function useAdvancedDoc(id: string | null) {
  return useQuery<AdvancedDocResult>({
    queryKey: ["advanced-docs", id],
    queryFn: () => fetchJson(`${API_BASE}/advanced-docs/${id}`),
    enabled: !!id,
    refetchInterval: (query) => {
      const d = query.state.data;
      if (d?.section_jobs?.technical_specs === "running") return 2500;
      const status = d?.status;
      if (status === "ready" || status === "error" || status === "partial")
        return false;
      return 3000;
    },
  });
}

// ─── Upload mutation ────────────────────────────────────────────────────────

async function uploadChunkWithRetry(
  url: string,
  form: FormData,
  maxRetries = 3,
): Promise<void> {
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const res = await fetch(url, { method: "POST", body: form });
      if (res.ok) return;
      if (attempt === maxRetries - 1) {
        const text = await res.text();
        throw new Error(text || `Upload chunk failed (status ${res.status})`);
      }
    } catch (err) {
      if (attempt === maxRetries - 1) throw err;
    }
    await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
  }
}

export function useAdvancedDocUpload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ file, name }: { file: File; name: string }) => {
      const CHUNK_SIZE = 5 * 1024 * 1024;
      const totalChunks = Math.ceil(file.size / CHUNK_SIZE);

      const initRes = await fetch(`${API_BASE}/advanced-docs/upload/init`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: file.name,
          totalSize: file.size,
          totalChunks,
          name,
        }),
      });
      if (!initRes.ok) {
        const text = await initRes.text();
        throw new Error(text || "Upload init failed");
      }
      const { uploadId } = (await initRes.json()) as { uploadId: string };

      const keepaliveTimer = setInterval(() => {
        fetch(`${API_BASE}/advanced-docs/upload/keepalive`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ uploadId }),
        }).catch(() => {});
      }, 20_000);

      try {
        for (let i = 0; i < totalChunks; i++) {
          const start = i * CHUNK_SIZE;
          const end = Math.min(start + CHUNK_SIZE, file.size);
          const blob = file.slice(start, end);

          const form = new FormData();
          form.append("file", blob, file.name);
          form.append("uploadId", uploadId);
          form.append("chunkIndex", String(i));

          await uploadChunkWithRetry(
            `${API_BASE}/advanced-docs/upload/chunk`,
            form,
          );
        }
      } finally {
        clearInterval(keepaliveTimer);
      }

      const finalRes = await fetch(`${API_BASE}/advanced-docs/upload/finalize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uploadId, name }),
      });
      if (!finalRes.ok) {
        const text = await finalRes.text();
        throw new Error(text || "Upload finalize failed");
      }
      return finalRes.json() as Promise<{
        id: string;
        name: string;
        status: string;
      }>;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["advanced-docs"] });
    },
  });
}

// ─── Delete mutation ────────────────────────────────────────────────────────

export function useRegenerateAdvancedTechnicalSpecs(docId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      if (!docId) throw new Error("No project selected");
      const res = await fetch(
        `${API_BASE}/advanced-docs/${docId}/regenerate/technical-specs`,
        { method: "POST" },
      );
      if (!res.ok) {
        let msg = res.statusText;
        try {
          const body = (await res.json()) as { detail?: string | { msg?: string }[] };
          if (typeof body.detail === "string") msg = body.detail;
          else if (Array.isArray(body.detail))
            msg = body.detail.map((x) => (typeof x === "object" && x && "msg" in x ? String((x as { msg: string }).msg) : String(x))).join("; ");
        } catch {
          /* use statusText */
        }
        throw new Error(msg);
      }
      return res.json() as Promise<{ accepted: boolean; section: string }>;
    },
    onSuccess: () => {
      if (docId) {
        qc.invalidateQueries({ queryKey: ["advanced-docs", docId] });
        qc.invalidateQueries({ queryKey: ["advanced-docs"] });
      }
    },
  });
}

export function useDeleteAdvancedDoc() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${API_BASE}/advanced-docs/${id}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error("Delete failed");
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["advanced-docs"] });
    },
  });
}

// ─── Export helper ───────────────────────────────────────────────────────────

export async function exportAdvancedDoc(id: string) {
  const data = await fetchJson<AdvancedDocResult>(`${API_BASE}/advanced-docs/${id}`);
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const safeName = (data.name || id).replace(/[^a-z0-9]/gi, "_");
  a.download = `${safeName}_report.json`;
  a.click();
  URL.revokeObjectURL(url);
}

// ─── Import mutation ─────────────────────────────────────────────────────────

export function useImportAdvancedDoc() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const text = await file.text();
      const data = JSON.parse(text);
      const res = await fetch(`${API_BASE}/advanced-docs-import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      if (!res.ok) {
        const errText = await res.text();
        throw new Error(errText || res.statusText);
      }
      return res.json() as Promise<{ id: string; name: string; status: string }>;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["advanced-docs"] });
    },
  });
}

// ─── SSE streaming hook ─────────────────────────────────────────────────────

export function useAdvancedDocStream(id: string | null) {
  const [steps, setSteps] = useState<StreamStep[]>(STEP_DEFAULTS);
  const [data, setData] = useState<Partial<AdvancedDocResult>>({});
  const [isStreaming, setIsStreaming] = useState(false);
  const [isDone, setIsDone] = useState(false);
  const [finalStatus, setFinalStatus] = useState<string>("");
  const [error, setError] = useState<string | undefined>();
  const esRef = useRef<EventSource | null>(null);
  const qc = useQueryClient();

  const updateStep = useCallback(
    (stepKey: string, patch: Partial<StreamStep>) => {
      setSteps((prev) =>
        prev.map((s) => (s.step === stepKey ? { ...s, ...patch } : s)),
      );
    },
    [],
  );

  const connect = useCallback(
    (docId: string) => {
      if (esRef.current) {
        esRef.current.close();
      }
      setSteps(STEP_DEFAULTS.map((s) => ({ ...s })));
      setData({});
      setIsDone(false);
      setFinalStatus("");
      setError(undefined);
      setIsStreaming(true);

      const es = new EventSource(`${API_BASE}/advanced-docs/${docId}/stream`);
      esRef.current = es;

      es.addEventListener("connected", (e) => {
        const d = JSON.parse((e as MessageEvent).data);
        setData((prev) => ({ ...prev, id: d.id, name: d.name }));
      });

      es.addEventListener("step_start", (e) => {
        const d = JSON.parse((e as MessageEvent).data);
        updateStep(d.step, {
          status: "running",
          label: d.label || undefined,
        });
      });

      es.addEventListener("extraction_progress", (e) => {
        const d = JSON.parse((e as MessageEvent).data) as {
          phase?: string;
          current?: number;
          total?: number;
          percent?: number;
          files_scanned?: number;
        };
        if (d.phase === "index") {
          const n = d.files_scanned ?? 0;
          updateStep("extraction", {
            summary:
              n > 0
                ? `Indexing… ${n.toLocaleString()} paths scanned`
                : "Indexing extracted files…",
          });
          return;
        }
        const cur = d.current ?? 0;
        const tot = Math.max(d.total ?? 1, 1);
        const pct = d.percent ?? Math.round((100 * cur) / tot);
        updateStep("extraction", {
          summary: `Unpacking archive… ${pct}% (${cur.toLocaleString()} / ${tot.toLocaleString()} entries)`,
        });
      });

      es.addEventListener("step_complete", (e) => {
        const d = JSON.parse((e as MessageEvent).data);
        updateStep(d.step, {
          status: "complete",
          summary: d.summary || undefined,
        });

        if (d.data) {
          setData((prev) => {
            const next = { ...prev };
            if (d.step === "extraction") {
              next.file_count = d.data.file_count;
              next.files = d.data.files;
              next.project_tree = d.data.project_tree;
            } else if (d.step === "knowledge_graph") {
              next.knowledge_graph = d.data;
            } else if (d.step === "features") {
              next.features = d.data;
            } else if (d.step === "cross_validation") {
              next.cross_validation = d.data;
            } else if (d.step === "feature_connections") {
              next.feature_connections = d.data;
            } else if (d.step === "flow_diagrams") {
              next.flow_diagrams = d.data;
            } else if (d.step === "technical_specs") {
              next.technical_specs = d.data;
            } else if (d.step === "documentation") {
              next.documentation = d.data.documentation;
            } else if (d.step === "quality_check") {
              next.quality_score = d.data;
            }
            return next;
          });
        }
      });

      es.addEventListener("step_error", (e) => {
        const d = JSON.parse((e as MessageEvent).data);
        updateStep(d.step, { status: "error", error: d.error });
      });

      es.addEventListener("step_skip", (e) => {
        const d = JSON.parse((e as MessageEvent).data);
        updateStep(d.step, { status: "skipped", error: d.reason });
      });

      es.addEventListener("done", (e) => {
        const d = JSON.parse((e as MessageEvent).data);
        setFinalStatus(d.status);
        if (d.error) setError(d.error);
        setIsDone(true);
        setIsStreaming(false);
        es.close();
        esRef.current = null;
        qc.invalidateQueries({ queryKey: ["advanced-docs"] });
        qc.invalidateQueries({ queryKey: ["advanced-docs", docId] });
      });

      es.onerror = () => {
        es.close();
        esRef.current = null;
        setIsStreaming(false);
        if (!isDone) {
          setIsDone(true);
          qc.invalidateQueries({ queryKey: ["advanced-docs"] });
          qc.invalidateQueries({ queryKey: ["advanced-docs", docId] });
        }
      };
    },
    [updateStep, qc, isDone],
  );

  const disconnect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  useEffect(() => {
    return () => {
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, []);

  return {
    steps,
    data,
    isStreaming,
    isDone,
    finalStatus,
    error,
    connect,
    disconnect,
  };
}
