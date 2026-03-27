import { useState, useCallback, useEffect } from "react";
import { useRoute, Link } from "wouter";
import { 
  useSolutionDetails, 
  useKnowledgeGraph, 
  useEntities, 
  useWorkflows, 
  usePlugins,
  useFunctionalFlows,
  useDocs,
  useGenerateDocumentation,
  useVerifyDocumentation
} from "@/hooks/use-solutions";
import { KnowledgeGraphViewer } from "@/components/KnowledgeGraphViewer";
import { MarkdownViewer, CollapsibleSection } from "@/components/MarkdownViewer";
import { format } from "date-fns";
import { 
  ArrowLeft, Database, Zap, Settings, Share2, FileText, 
  CheckCircle2, AlertTriangle, Loader2, Sparkles, RefreshCcw, FileSearch,
  Download, Play, CheckCheck, ListChecks, Link2, Waypoints, MessageSquare, Send,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/hooks/use-toast";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

// All 15 documentation sections across 7 groups
const ALL_DOC_SECTIONS = [
  { key: "doc_purpose", label: "1.1 Purpose of the Document", order: 1, group: "1. Overview" },
  { key: "intended_audience", label: "1.2 Intended Audience", order: 2, group: "1. Overview" },
  { key: "requirement_description", label: "2.1 Requirement Description", order: 3, group: "2. Design Overview" },
  { key: "functional_design_overview", label: "2.2 Functional Design Overview", order: 4, group: "2. Design Overview" },
  { key: "architectural_diagram", label: "2.3 Process Detailed Architectural Diagram", order: 5, group: "2. Design Overview" },
  { key: "process_flow_description", label: "2.4 Process Flow Description", order: 6, group: "2. Design Overview" },
  { key: "action_menu_items", label: "3.1 Action Menu Items", order: 7, group: "3. Detailed Technical Design" },
  { key: "tables", label: "3.2 Tables", order: 8, group: "3. Detailed Technical Design" },
  { key: "forms", label: "3.3 Forms", order: 9, group: "3. Detailed Technical Design" },
  { key: "classes", label: "3.4 Classes", order: 10, group: "3. Detailed Technical Design" },
  { key: "digital_signature_utility", label: "3.5 Digital Signature Utility", order: 11, group: "3. Detailed Technical Design" },
  { key: "component_overview", label: "4. Component Overview", order: 12, group: "4. Component Overview" },
  { key: "how_everything_links", label: "5. How Everything Links Together", order: 13, group: "5. How Everything Links Together" },
  { key: "feature_list", label: "6. Features", order: 14, group: "6. Features" },
  { key: "feature_flows", label: "7. Feature Flows", order: 15, group: "7. Feature Flows" },
];

const TABS = [
  { id: 'overview', label: 'Overview', icon: FileSearch },
  { id: 'graph', label: 'Knowledge Graph', icon: Share2 },
  { id: 'entities', label: 'Entities', icon: Database },
  { id: 'workflows', label: 'Workflows', icon: Zap },
  { id: 'plugins', label: 'Plugins', icon: Settings },
  { id: 'features', label: 'Features', icon: ListChecks },
  { id: 'feature_connections', label: 'Feature connections', icon: Link2 },
  { id: 'flow_diagram', label: 'Flow diagrams', icon: Waypoints },
  { id: 'docs', label: 'AI Documentation', icon: FileText },
  { id: 'chat', label: 'Project Q&A', icon: MessageSquare },
] as const;

const INSIGHT_TAB_IDS = new Set<string>(["features", "feature_connections", "flow_diagram"]);

type InsightEntry = { content?: string; generatedAt?: string };
type InsightsState = Record<string, InsightEntry | undefined>;

type ChatTurn = { role: "user" | "assistant"; content: string };

export default function SolutionDetail() {
  const [, params] = useRoute("/solutions/:id");
  const id = params?.id || "";
  const initialTab = typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('tab') || 'overview' : 'overview';
  const [activeTab, setActiveTab] = useState(initialTab);
  const { toast } = useToast();

  const { data: solution, isLoading: isLoadingSolution } = useSolutionDetails(id);
  const { data: graph } = useKnowledgeGraph(id, activeTab === 'graph');
  const { data: entities } = useEntities(id, activeTab === 'entities');
  const { data: workflows } = useWorkflows(id, activeTab === 'workflows');
  const { data: plugins } = usePlugins(id, activeTab === 'plugins');
  const { data: flows } = useFunctionalFlows(id, activeTab === 'overview');
  const { data: docs, isLoading: isLoadingDocs } = useDocs(id, activeTab === 'docs');

  const queryClient = useQueryClient();

  const insightsEnabled = INSIGHT_TAB_IDS.has(activeTab);
  const { data: insights, isLoading: isLoadingInsights } = useQuery({
    queryKey: [`/api/py-api/solutions/${id}/insights`],
    queryFn: async (): Promise<InsightsState> => {
      const r = await fetch(`/api/py-api/solutions/${id}/insights`);
      if (!r.ok) return {};
      return r.json();
    },
    enabled: !!id && insightsEnabled,
  });

  const [chatMessages, setChatMessages] = useState<ChatTurn[]>([]);
  const [chatInput, setChatInput] = useState("");

  useEffect(() => {
    setChatMessages([]);
    setChatInput("");
  }, [id]);

  const chatMutation = useMutation({
    mutationFn: async (payload: { message: string; history: ChatTurn[] }) => {
      const r = await fetch(`/api/py-api/solutions/${id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: payload.message,
          history: payload.history.map((m) => ({ role: m.role, content: m.content })),
        }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: "Request failed" }));
        const d = err.detail;
        throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x: { msg?: string }) => x.msg).join(", ") : "Request failed");
      }
      return r.json() as { answer: string };
    },
  });

  const handleSendChat = useCallback(async () => {
    const text = chatInput.trim();
    if (!text || !id || solution?.status !== "ready" || chatMutation.isPending) return;
    const historySnapshot = chatMessages;
    setChatInput("");
    setChatMessages((prev) => [...prev, { role: "user", content: text }]);
    try {
      const data = await chatMutation.mutateAsync({ message: text, history: historySnapshot });
      setChatMessages((prev) => [...prev, { role: "assistant", content: data.answer }]);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Something went wrong";
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", content: `**Could not get an answer.** ${msg}` },
      ]);
      toast({ title: "Q&A failed", description: msg, variant: "destructive" });
    }
  }, [chatInput, id, solution?.status, chatMessages, chatMutation, toast]);

  const insightGenerateMutation = useMutation({
    mutationFn: async (insightType: string) => {
      const r = await fetch(`/api/py-api/solutions/${id}/insights/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ insightType: insightType }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: "Generation failed" }));
        throw new Error(err.detail || "Generation failed");
      }
      return r.json() as InsightEntry;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [`/api/py-api/solutions/${id}/insights`] });
    },
  });
  
  const generateMutation = useGenerateDocumentation();
  const verifyMutation = useVerifyDocumentation();

  const [selectedSections, setSelectedSections] = useState<string[]>(
    ALL_DOC_SECTIONS.map(s => s.key)
  );
  const [downloading, setDownloading] = useState<string | null>(null);
  const [generatingSections, setGeneratingSections] = useState<Set<string>>(new Set());
  const [generatedSections, setGeneratedSections] = useState<Set<string>>(new Set());

  // Generate a single section via dedicated API
  const handleGenerateSection = useCallback(async (sectionKey: string) => {
    setGeneratingSections(prev => new Set(prev).add(sectionKey));
    try {
      const res = await fetch(`/api/py-api/solutions/${id}/generate-section/${sectionKey}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Unknown error" }));
        throw new Error(err.detail || "Generation failed");
      }
      setGeneratedSections(prev => new Set(prev).add(sectionKey));
      // Invalidate docs cache to refresh
      queryClient.invalidateQueries({ queryKey: [`/api/py-api/solutions/${id}/docs`] });
      queryClient.invalidateQueries({ queryKey: [`/api/py-api/solutions/${id}`] });
      toast({ title: `Section generated`, description: ALL_DOC_SECTIONS.find(s => s.key === sectionKey)?.label });
    } catch (err: any) {
      toast({ title: "Section generation failed", description: err.message, variant: "destructive" });
    } finally {
      setGeneratingSections(prev => {
        const next = new Set(prev);
        next.delete(sectionKey);
        return next;
      });
    }
  }, [id, queryClient, toast]);

  // Generate all selected sections one-by-one (sequential per-section API calls)
  const handleGenerateAllSequential = useCallback(async () => {
    for (const sectionKey of selectedSections) {
      if (generatingSections.size > 0) break; // abort if something went wrong
      await handleGenerateSection(sectionKey);
    }
    toast({ title: "All sections generated", description: `${selectedSections.length} sections completed.` });
  }, [selectedSections, generatingSections, handleGenerateSection, toast]);

  // Bulk generate (existing behavior — single API call)
  const handleGenerateBulk = () => {
    generateMutation.mutate({ 
      id, 
      data: { sections: selectedSections as any } 
    }, {
      onSuccess: () => {
        toast({ title: "Documentation generated", description: `${selectedSections.length} sections created.` });
        setActiveTab('docs');
      },
      onError: (err) => {
        toast({ title: "Generation failed", description: err.data?.error || "Unknown error", variant: "destructive" });
      }
    });
  };

  const handleDownload = async (format: "docx" | "pdf") => {
    setDownloading(format);
    try {
      const res = await fetch(`/api/py-api/solutions/${id}/download/${format}`);
      if (!res.ok) throw new Error("Download failed");
      const blob = await res.blob();
      const disposition = res.headers.get("Content-Disposition") || "";
      const filenameMatch = disposition.match(/filename="?([^";\n]+)"?/);
      const fallbackExt = format === "docx" ? "docx" : "pdf";
      const filename = filenameMatch?.[1] || `${solution?.name || "Documentation"}.${fallbackExt}`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast({ title: `${format.toUpperCase()} downloaded successfully` });
    } catch {
      toast({ title: "Download failed", description: "Could not generate the file.", variant: "destructive" });
    } finally {
      setDownloading(null);
    }
  };

  if (isLoadingSolution) {
    return (
      <div className="flex items-center justify-center h-full min-h-[50vh]">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  if (!solution) {
    return (
      <div className="text-center py-20">
        <h2 className="text-2xl font-bold text-foreground">Solution not found</h2>
        <Link href="/" className="text-primary hover:underline mt-4 inline-block">Return to Dashboard</Link>
      </div>
    );
  }

  const handleGenerate = () => {
    handleGenerateBulk();
  };

  const handleVerify = () => {
    verifyMutation.mutate({ id }, {
      onSuccess: (data) => {
        toast({ 
          title: "Verification Complete", 
          description: `Score: ${data.score}%. Found ${data.issues.length} issues.` 
        });
      }
    });
  };

  const renderGenAiInsightPanel = (
    tabId: "features" | "feature_connections" | "flow_diagram",
    heading: string,
    description: string,
  ) => {
    const entry = insights?.[tabId];
    const pendingKey = insightGenerateMutation.variables as string | undefined;
    const busy = insightGenerateMutation.isPending && pendingKey === tabId;

    return (
      <div className="space-y-6 animate-in slide-in-from-bottom-4 duration-500">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4 bg-card border border-border rounded-xl p-6">
          <div className="space-y-1">
            <h3 className="text-lg font-semibold flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-primary" />
              {heading}
            </h3>
            <p className="text-sm text-muted-foreground max-w-2xl">{description}</p>
            {entry?.generatedAt && (
              <p className="text-xs text-muted-foreground pt-1">
                Last generated {format(new Date(entry.generatedAt), "PPp")}
              </p>
            )}
          </div>
          <Button
            onClick={() => {
              insightGenerateMutation.mutate(tabId, {
                onError: (err: Error) =>
                  toast({
                    title: "Generation failed",
                    description: err.message,
                    variant: "destructive",
                  }),
                onSuccess: () =>
                  toast({
                    title: "Insight ready",
                    description: `${heading} was generated with PwC Gen AI.`,
                  }),
              });
            }}
            disabled={busy || solution.status !== "ready"}
            className="shrink-0 bg-primary text-primary-foreground hover:bg-primary/90"
          >
            {busy ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Generating…
              </>
            ) : (
              <>
                <Sparkles className="w-4 h-4 mr-2" />
                Generate with Gen AI
              </>
            )}
          </Button>
        </div>

        {isLoadingInsights ? (
          <div className="flex justify-center py-16">
            <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
          </div>
        ) : entry?.content ? (
          <div className="bg-card border border-border rounded-xl p-6 sm:p-8 min-h-[200px] min-w-0 max-h-[calc(100vh-10rem)] overflow-y-auto overflow-x-auto">
            <MarkdownViewer content={entry.content} />
          </div>
        ) : (
          <div className="border border-dashed border-border rounded-xl p-12 text-center text-muted-foreground max-w-xl mx-auto">
            <Sparkles className="w-10 h-10 mx-auto mb-3 opacity-40" />
            <p>
              No content yet. Choose <strong>Generate with Gen AI</strong> to produce this view from the
              knowledge graph and functional flows using <strong>PwC Gen AI</strong>.
            </p>
          </div>
        )}
      </div>
    );
  };

  const renderTabContent = () => {
    switch (activeTab) {
      case 'overview':
        return (
          <div className="space-y-8 animate-in slide-in-from-bottom-4 duration-500">
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="bg-card border border-border rounded-xl p-6 flex flex-col items-center justify-center text-center">
                <Database className="w-8 h-8 text-blue-400 mb-3" />
                <span className="text-3xl font-display font-bold text-foreground">{solution.entityCount}</span>
                <span className="text-sm text-muted-foreground">Entities Found</span>
              </div>
              <div className="bg-card border border-border rounded-xl p-6 flex flex-col items-center justify-center text-center">
                <Zap className="w-8 h-8 text-orange-400 mb-3" />
                <span className="text-3xl font-display font-bold text-foreground">{solution.workflowCount}</span>
                <span className="text-sm text-muted-foreground">Workflows</span>
              </div>
              <div className="bg-card border border-border rounded-xl p-6 flex flex-col items-center justify-center text-center">
                <Settings className="w-8 h-8 text-emerald-400 mb-3" />
                <span className="text-3xl font-display font-bold text-foreground">{solution.pluginCount}</span>
                <span className="text-sm text-muted-foreground">Plugins</span>
              </div>
              <div className="bg-card border border-border rounded-xl p-6 flex flex-col items-center justify-center text-center">
                <FileText className="w-8 h-8 text-purple-400 mb-3" />
                <span className="text-3xl font-display font-bold text-foreground">
                  {solution.hasDocumentation ? 'Ready' : 'None'}
                </span>
                <span className="text-sm text-muted-foreground">Documentation</span>
              </div>
            </div>

            <div className="bg-card border border-border rounded-xl p-6">
              <h3 className="text-lg font-semibold mb-4">Solution Metadata</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4">
                <div>
                  <div className="text-sm text-muted-foreground">Version</div>
                  <div className="font-medium">{solution.metadata?.solutionVersion || 'N/A'}</div>
                </div>
                <div>
                  <div className="text-sm text-muted-foreground">Publisher</div>
                  <div className="font-medium">{solution.metadata?.publisher || 'N/A'}</div>
                </div>
                <div className="md:col-span-2">
                  <div className="text-sm text-muted-foreground">Description</div>
                  <div className="font-medium">{solution.metadata?.description || 'No description provided.'}</div>
                </div>
              </div>
            </div>

            {flows && flows.length > 0 && (
              <div className="bg-card border border-border rounded-xl p-6">
                <h3 className="text-lg font-semibold mb-4">Detected Functional Flows</h3>
                <div className="space-y-4">
                  {flows.map((flow, i) => (
                    <div key={i} className="flex flex-col md:flex-row gap-4 p-4 rounded-lg bg-muted/30 border border-border/50">
                      <div className="min-w-[150px] font-medium text-primary">{flow.entity}</div>
                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-2">
                          <span className="text-sm px-2 py-0.5 bg-orange-500/10 text-orange-400 border border-orange-500/20 rounded">Workflow: {flow.workflow}</span>
                          {flow.plugins.map(p => (
                            <span key={p} className="text-sm px-2 py-0.5 bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded">Plugin: {p}</span>
                          ))}
                        </div>
                        <p className="text-sm text-muted-foreground">{flow.description}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        );

      case 'graph':
        return (
          <div className="h-[700px] animate-in slide-in-from-bottom-4 duration-500">
            {graph ? (
              <KnowledgeGraphViewer
                data={graph}
                projectKind={solution?.metadata?.projectKind ?? undefined}
              />
            ) : (
              <div className="flex items-center justify-center h-full border border-dashed rounded-xl border-border bg-card/50">
                <p className="text-muted-foreground">Loading Knowledge Graph...</p>
              </div>
            )}
          </div>
        );

      case 'entities':
        return (
          <div className="space-y-4 animate-in slide-in-from-bottom-4 duration-500">
            {entities?.map(entity => (
              <div key={entity.name} className="bg-card border border-border rounded-xl p-5">
                <div className="flex justify-between items-start mb-4 gap-4">
                  <h3 className="text-lg font-semibold text-blue-400 break-all min-w-0">{entity.displayName || entity.name} <span className="text-sm font-normal text-muted-foreground ml-2">({entity.name})</span></h3>
                  <div className="flex gap-2 shrink-0">
                    {entity.workflows && entity.workflows.length > 0 && <span className="text-xs bg-muted px-2 py-1 rounded-md">{entity.workflows.length} workflows</span>}
                    {entity.plugins && entity.plugins.length > 0 && <span className="text-xs bg-muted px-2 py-1 rounded-md">{entity.plugins.length} plugins</span>}
                  </div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
                  {entity.fields.slice(0, 12).map(field => (
                    <div key={field.name} className="flex justify-between text-sm p-2 bg-muted/30 rounded border border-border/30">
                      <span className="text-foreground truncate pr-2" title={field.displayName || field.name}>{field.displayName || field.name}</span>
                      <span className="text-muted-foreground shrink-0">{field.type}</span>
                    </div>
                  ))}
                  {entity.fields.length > 12 && (
                    <div className="text-sm p-2 text-muted-foreground italic flex items-center justify-center bg-muted/10 rounded border border-border/30">
                      + {entity.fields.length - 12} more fields
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        );

      case 'workflows':
        return (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-in slide-in-from-bottom-4 duration-500">
            {workflows?.map(wf => (
              <div key={wf.name} className="bg-card border border-border rounded-xl p-5">
                <h3 className="text-lg font-semibold text-orange-400 mb-2">{wf.name}</h3>
                <div className="space-y-2 mb-4">
                  {wf.triggerEntity && (
                    <div className="text-sm flex"><span className="w-20 text-muted-foreground">Entity:</span> <span className="text-foreground">{wf.triggerEntity}</span></div>
                  )}
                  {wf.trigger && (
                    <div className="text-sm flex"><span className="w-20 text-muted-foreground">Trigger:</span> <span className="text-foreground">{wf.trigger}</span></div>
                  )}
                </div>
                <div className="border-t border-border/50 pt-3">
                  <span className="text-xs text-muted-foreground uppercase tracking-wider mb-2 block">Steps ({wf.steps.length})</span>
                  <div className="flex flex-wrap gap-2">
                    {wf.steps.map((step, i) => (
                      <span key={i} className="text-xs bg-muted px-2 py-1 rounded text-foreground">{step}</span>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        );
        
      case 'plugins':
        return (
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4 animate-in slide-in-from-bottom-4 duration-500">
            {plugins?.map(pl => (
              <div key={pl.name} className="bg-card border border-border rounded-xl p-5">
                <h3 className="text-lg font-semibold text-emerald-400 mb-3 break-all" title={pl.name}>{pl.name}</h3>
                <div className="space-y-3">
                  <div className="bg-muted/30 p-3 rounded-lg border border-border/50">
                    <div className="text-xs text-muted-foreground mb-1">Target Entity</div>
                    <div className="font-medium text-sm">{pl.triggerEntity || 'Global/None'}</div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="bg-muted/30 p-3 rounded-lg border border-border/50">
                      <div className="text-xs text-muted-foreground mb-1">Message</div>
                      <div className="font-medium text-sm">{pl.operation || 'Unknown'}</div>
                    </div>
                    <div className="bg-muted/30 p-3 rounded-lg border border-border/50">
                      <div className="text-xs text-muted-foreground mb-1">Stage</div>
                      <div className="font-medium text-sm">{pl.stage || 'Unknown'}</div>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        );

      case "features":
        return renderGenAiInsightPanel(
          "features",
          "Feature list",
          "A structured list of capabilities inferred from entities, workflows, plugins, forms, and relationships in the knowledge graph. Generated on demand with PwC Gen AI.",
        );

      case "feature_connections":
        return renderGenAiInsightPanel(
          "feature_connections",
          "How features connect",
          "Explains cross-feature links using graph relationships and detected functional flows, including an optional Mermaid map. Generated with PwC Gen AI.",
        );

      case "flow_diagram":
        return renderGenAiInsightPanel(
          "flow_diagram",
          "Detailed flow diagrams",
          "Narrative plus detailed Mermaid flowcharts for major paths through the solution. Optimized for end-to-end visualization. Generated with PwC Gen AI.",
        );

      case "chat":
        return (
          <div className="flex flex-col gap-4 animate-in slide-in-from-bottom-4 duration-500 max-w-4xl mx-auto w-full min-h-[min(70vh,640px)]">
            <div className="bg-card border border-border rounded-xl p-5 sm:p-6 flex flex-col flex-1 min-h-[480px]">
              <div className="flex items-start gap-3 mb-4">
                <div className="p-2 rounded-lg bg-primary/10 text-primary shrink-0">
                  <MessageSquare className="w-5 h-5" />
                </div>
                <div className="space-y-1 min-w-0">
                  <h3 className="text-lg font-semibold text-foreground">Project Q&A</h3>
                  <p className="text-sm text-muted-foreground">
                    Short, business-relevant answers tied to concrete components (entities, workflows, plugins) from this
                    upload only — no web or other projects.
                  </p>
                </div>
              </div>

              <div className="flex-1 overflow-y-auto rounded-lg border border-border/60 bg-muted/20 min-h-[280px] max-h-[min(52vh,520px)] p-4 space-y-4">
                {chatMessages.length === 0 && !chatMutation.isPending ? (
                  <p className="text-sm text-muted-foreground text-center py-12 px-4">
                    Ask something specific about{" "}
                    <span className="text-foreground font-medium">{solution.name}</span>
                    — e.g. which workflows or plugins involve an entity, or how parts connect in the graph.
                  </p>
                ) : null}
                {chatMessages.map((m, i) => (
                  <div
                    key={i}
                    className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
                  >
                    <div
                      className={`max-w-[92%] rounded-xl px-4 py-3 text-sm ${
                        m.role === "user"
                          ? "bg-primary text-primary-foreground"
                          : "bg-card border border-border text-foreground"
                      }`}
                    >
                      {m.role === "assistant" ? (
                        <div className="min-w-0 text-left">
                          <MarkdownViewer content={m.content} />
                        </div>
                      ) : (
                        <p className="whitespace-pre-wrap break-words">{m.content}</p>
                      )}
                    </div>
                  </div>
                ))}
                {chatMutation.isPending ? (
                  <div className="flex justify-start">
                    <div className="rounded-xl px-4 py-3 bg-card border border-border flex items-center gap-2 text-sm text-muted-foreground">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Answering from project data…
                    </div>
                  </div>
                ) : null}
              </div>

              <div className="mt-4 space-y-2">
                <Textarea
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  placeholder={
                    solution.status === "ready"
                      ? "Ask a question about this project…"
                      : "Available when parsing is complete."
                  }
                  disabled={solution.status !== "ready" || chatMutation.isPending}
                  className="min-h-[100px] resize-y bg-background"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      if (chatInput.trim() && solution.status === "ready" && !chatMutation.isPending) {
                        void handleSendChat();
                      }
                    }
                  }}
                />
                <div className="flex justify-end gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={chatMessages.length === 0 || chatMutation.isPending}
                    onClick={() => setChatMessages([])}
                  >
                    Clear thread
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    className="gap-2 bg-primary text-primary-foreground hover:bg-primary/90"
                    disabled={
                      solution.status !== "ready" || chatMutation.isPending || !chatInput.trim()
                    }
                    onClick={() => void handleSendChat()}
                  >
                    {chatMutation.isPending ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Send className="w-4 h-4" />
                    )}
                    Send
                  </Button>
                </div>
              </div>
            </div>
          </div>
        );

      case 'docs':
        const sectionGroups = ALL_DOC_SECTIONS.reduce((acc, s) => {
          if (!acc[s.group]) acc[s.group] = [];
          acc[s.group].push(s);
          return acc;
        }, {} as Record<string, typeof ALL_DOC_SECTIONS>);

        return (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 h-[800px] animate-in slide-in-from-bottom-4 duration-500">
            {/* Controls sidebar */}
            <div className="lg:col-span-4 space-y-4 flex flex-col overflow-y-auto pr-1">
              <div className="bg-card border border-border rounded-xl p-5">
                <h3 className="font-semibold mb-3 flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-primary" />
                  CRM Documentation Generator
                </h3>
                <p className="text-xs text-muted-foreground mb-4">
                  Select sections to generate. Use <strong>per-section</strong> mode for incremental generation with chunking, or <strong>bulk</strong> to generate all at once.
                </p>
                
                <div className="space-y-4 mb-4">
                  {Object.entries(sectionGroups).map(([groupName, sections]) => (
                    <div key={groupName}>
                      <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1.5">{groupName}</div>
                      <div className="space-y-1">
                        {sections.map(section => {
                          const isGenerating = generatingSections.has(section.key);
                          const isGenerated = generatedSections.has(section.key) || 
                            (docs?.sections?.some(s => s.slug === section.key));

                          return (
                            <div key={section.key} className="flex items-center gap-2 p-1.5 hover:bg-muted/50 rounded-lg transition-colors group">
                              <input 
                                type="checkbox" 
                                checked={selectedSections.includes(section.key)}
                                onChange={(e) => {
                                  if (e.target.checked) setSelectedSections([...selectedSections, section.key]);
                                  else setSelectedSections(selectedSections.filter(s => s !== section.key));
                                }}
                                className="w-3.5 h-3.5 rounded border-border text-primary focus:ring-primary/50 bg-background shrink-0"
                              />
                              <span className="text-xs flex-1 truncate" title={section.label}>
                                <span className="text-muted-foreground mr-1">{section.order}.</span>
                                {section.label}
                              </span>
                              {isGenerated && !isGenerating && (
                                <CheckCheck className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                              )}
                              <button
                                onClick={() => handleGenerateSection(section.key)}
                                disabled={isGenerating || generateMutation.isPending}
                                className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-primary/10 text-primary disabled:opacity-30 shrink-0"
                                title={`Generate "${section.label}" individually`}
                              >
                                {isGenerating ? (
                                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                ) : (
                                  <Play className="w-3.5 h-3.5" />
                                )}
                              </button>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="flex items-center gap-2 mb-3">
                  <button
                    onClick={() => setSelectedSections(ALL_DOC_SECTIONS.map(s => s.key))}
                    className="text-xs text-primary hover:underline"
                  >
                    Select All
                  </button>
                  <span className="text-muted-foreground text-xs">|</span>
                  <button
                    onClick={() => setSelectedSections([])}
                    className="text-xs text-primary hover:underline"
                  >
                    Deselect All
                  </button>
                  <span className="text-xs text-muted-foreground ml-auto">
                    {selectedSections.length}/{ALL_DOC_SECTIONS.length}
                  </span>
                </div>

                <div className="space-y-2">
                  <Button 
                    onClick={handleGenerateAllSequential}
                    disabled={generateMutation.isPending || generatingSections.size > 0 || selectedSections.length === 0}
                    className="w-full bg-primary text-primary-foreground hover:bg-primary/90"
                    size="sm"
                  >
                    {generatingSections.size > 0 ? (
                      <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> Generating Section...</>
                    ) : (
                      <><Play className="w-4 h-4 mr-2" /> Generate Per-Section (Recommended)</>
                    )}
                  </Button>
                  <Button 
                    onClick={handleGenerate}
                    disabled={generateMutation.isPending || generatingSections.size > 0 || selectedSections.length === 0}
                    variant="outline"
                    className="w-full"
                    size="sm"
                  >
                    {generateMutation.isPending ? (
                      <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> Generating All...</>
                    ) : (
                      'Bulk Generate (Single Call)'
                    )}
                  </Button>
                </div>
              </div>

              {docs?.verified !== undefined && (
                <div className="bg-card border border-border rounded-xl p-5">
                  <h3 className="font-semibold mb-4 flex items-center gap-2">
                    <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                    Verification
                  </h3>
                  <div className="flex items-center justify-between mb-4">
                    <span className="text-sm text-muted-foreground">Status</span>
                    <span className={`text-sm font-medium px-2 py-0.5 rounded ${docs.verified ? 'bg-emerald-500/10 text-emerald-400' : 'bg-amber-500/10 text-amber-500'}`}>
                      {docs.verified ? 'Verified' : 'Unverified'}
                    </span>
                  </div>
                  <Button 
                    onClick={handleVerify}
                    disabled={verifyMutation.isPending || !docs}
                    variant="outline"
                    className="w-full"
                    size="sm"
                  >
                    {verifyMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Run Verification Analysis'}
                  </Button>
                </div>
              )}
            </div>

            {/* Viewer area */}
            <div className="lg:col-span-8 bg-card border border-border rounded-xl overflow-hidden flex flex-col h-full">
              <div className="p-4 border-b border-border/50 bg-muted/20 flex justify-between items-center">
                <h3 className="font-medium text-foreground">Documentation Preview</h3>
                <div className="flex items-center gap-2">
                  {docs && <span className="text-xs text-muted-foreground mr-2">{docs.sections.length} sections • Generated {format(new Date(docs.generatedAt), "PP p")}</span>}
                  {docs && (
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => handleDownload("docx")}
                        disabled={downloading !== null}
                        className="text-xs gap-1.5"
                      >
                        {downloading === "docx" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
                        Word
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => handleDownload("pdf")}
                        disabled={downloading !== null}
                        className="text-xs gap-1.5"
                      >
                        {downloading === "pdf" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
                        PDF
                      </Button>
                    </>
                  )}
                </div>
              </div>
              <div className="flex-1 overflow-y-auto p-8 relative">
                {(generateMutation.isPending || generatingSections.size > 0) ? (
                  <div className="absolute inset-0 flex flex-col items-center justify-center bg-background/80 backdrop-blur-sm z-10">
                    <RefreshCcw className="w-12 h-12 text-primary animate-spin mb-4" />
                    <h3 className="text-xl font-display font-semibold">AI is writing documentation...</h3>
                    <p className="text-muted-foreground mt-2">
                      {generatingSections.size > 0 
                        ? `Generating: ${Array.from(generatingSections).map(k => ALL_DOC_SECTIONS.find(s => s.key === k)?.label).join(', ')}`
                        : 'Analyzing knowledge graph relationships and generating markdown.'
                      }
                    </p>
                    {generatedSections.size > 0 && (
                      <p className="text-sm text-emerald-400 mt-2">{generatedSections.size} section(s) completed</p>
                    )}
                  </div>
                ) : null}

                {isLoadingDocs ? (
                  <div className="h-full flex flex-col items-center justify-center">
                    <Loader2 className="w-8 h-8 animate-spin text-primary mb-3" />
                    <p className="text-muted-foreground text-sm">Loading documentation...</p>
                  </div>
                ) : docs ? (
                  <div className="space-y-3">
                    {[...docs.sections].sort((a,b)=>a.order-b.order).map((section, idx) => (
                      <CollapsibleSection
                        key={section.slug}
                        title={section.title}
                        slug={section.slug}
                        content={section.content}
                        defaultOpen={idx === 0}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="h-full flex flex-col items-center justify-center text-center text-muted-foreground">
                    <FileText className="w-16 h-16 mb-4 opacity-20" />
                    <p>No documentation generated yet.</p>
                    <p className="text-sm mt-1">Use the generator panel to create AI documentation section-by-section.</p>
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      
      default:
        return null;
    }
  };

  return (
    <div className="space-y-6 flex flex-col h-full animate-in fade-in duration-300">
      <div className="flex items-center gap-4">
        <Link href="/" className="p-2 -ml-2 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-display font-bold text-foreground">{solution.name}</h1>
            {solution.status === 'processing' && (
              <span className="flex items-center text-xs font-medium text-amber-500 bg-amber-500/10 px-2.5 py-1 rounded-full border border-amber-500/20">
                <Loader2 className="w-3 h-3 mr-1 animate-spin" /> Parsing Solution
              </span>
            )}
            {solution.status === 'error' && (
              <span className="flex items-center text-xs font-medium text-destructive bg-destructive/10 px-2.5 py-1 rounded-full border border-destructive/20">
                Processing Failed
              </span>
            )}
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            Uploaded {format(new Date(solution.uploadedAt), "MMMM do, yyyy 'at' h:mm a")}
          </p>
        </div>
      </div>

      <div className="rounded-2xl border border-border/80 bg-muted/40 p-2 sm:p-3 backdrop-blur-sm">
        <nav
          className="flex flex-wrap gap-2"
          aria-label="Solution sections"
        >
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`
                  inline-flex min-h-9 items-center gap-2 rounded-xl px-3 py-2 text-sm transition-all duration-200
                  focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background
                  ${isActive
                    ? "bg-background font-medium text-foreground shadow-sm ring-1 ring-border/70"
                    : "text-muted-foreground hover:bg-background/70 hover:text-foreground active:scale-[0.98]"
                  }
                `}
              >
                <Icon
                  className={`h-4 w-4 shrink-0 ${isActive ? "text-primary" : "opacity-80"}`}
                  aria-hidden
                />
                <span className="leading-tight">{tab.label}</span>
              </button>
            );
          })}
        </nav>
      </div>

      <div className="flex-1 overflow-y-auto">
        {renderTabContent()}
      </div>
    </div>
  );
}
