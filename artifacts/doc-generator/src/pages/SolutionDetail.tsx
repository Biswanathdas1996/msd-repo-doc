import { useState } from "react";
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
import { MarkdownViewer } from "@/components/MarkdownViewer";
import { format } from "date-fns";
import { 
  ArrowLeft, Database, Zap, Settings, Share2, FileText, 
  CheckCircle2, AlertTriangle, Loader2, Sparkles, RefreshCcw, FileSearch,
  Download
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/hooks/use-toast";

const TABS = [
  { id: 'overview', label: 'Overview', icon: FileSearch },
  { id: 'graph', label: 'Knowledge Graph', icon: Share2 },
  { id: 'entities', label: 'Entities', icon: Database },
  { id: 'workflows', label: 'Workflows', icon: Zap },
  { id: 'plugins', label: 'Plugins', icon: Settings },
  { id: 'docs', label: 'AI Documentation', icon: FileText },
];

export default function SolutionDetail() {
  const [, params] = useRoute("/solutions/:id");
  const id = params?.id || "";
  const [activeTab, setActiveTab] = useState('overview');
  const { toast } = useToast();

  const { data: solution, isLoading: isLoadingSolution } = useSolutionDetails(id);
  const { data: graph } = useKnowledgeGraph(id);
  const { data: entities } = useEntities(id);
  const { data: workflows } = useWorkflows(id);
  const { data: plugins } = usePlugins(id);
  const { data: flows } = useFunctionalFlows(id);
  const { data: docs, isLoading: isLoadingDocs } = useDocs(id);
  
  const generateMutation = useGenerateDocumentation();
  const verifyMutation = useVerifyDocumentation();

  const [selectedSections, setSelectedSections] = useState<string[]>([
    'overview', 'architecture', 'entities', 'workflows'
  ]);
  const [downloading, setDownloading] = useState<string | null>(null);

  const handleDownload = async (format: "docx" | "pdf") => {
    setDownloading(format);
    try {
      const res = await fetch(`/py-api/solutions/${id}/download/${format}`);
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
    generateMutation.mutate({ 
      id, 
      data: { sections: selectedSections as any } 
    }, {
      onSuccess: () => {
        toast({ title: "Documentation generation started", description: "This might take a few minutes." });
        setActiveTab('docs');
      },
      onError: (err) => {
        toast({ title: "Generation failed", description: err.data?.error || "Unknown error", variant: "destructive" });
      }
    });
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
            {graph ? <KnowledgeGraphViewer data={graph} /> : (
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
                <div className="flex justify-between items-center mb-4">
                  <h3 className="text-lg font-semibold text-blue-400">{entity.displayName || entity.name} <span className="text-sm font-normal text-muted-foreground ml-2">({entity.name})</span></h3>
                  <div className="flex gap-2">
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
                <h3 className="text-lg font-semibold text-emerald-400 mb-3 truncate" title={pl.name}>{pl.name}</h3>
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

      case 'docs':
        return (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 h-[800px] animate-in slide-in-from-bottom-4 duration-500">
            {/* Controls sidebar */}
            <div className="lg:col-span-3 space-y-6 flex flex-col">
              <div className="bg-card border border-border rounded-xl p-5">
                <h3 className="font-semibold mb-4 flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-primary" />
                  Generator
                </h3>
                <p className="text-sm text-muted-foreground mb-4">
                  Select the sections you want the AI to generate based on the knowledge graph.
                </p>
                
                <div className="space-y-2 mb-6">
                  {['overview', 'architecture', 'entities', 'workflows', 'functional_flow'].map(section => (
                    <label key={section} className="flex items-center gap-3 p-2 hover:bg-muted/50 rounded-lg cursor-pointer transition-colors">
                      <input 
                        type="checkbox" 
                        checked={selectedSections.includes(section)}
                        onChange={(e) => {
                          if (e.target.checked) setSelectedSections([...selectedSections, section]);
                          else setSelectedSections(selectedSections.filter(s => s !== section));
                        }}
                        className="w-4 h-4 rounded border-border text-primary focus:ring-primary/50 bg-background"
                      />
                      <span className="text-sm capitalize">{section.replace('_', ' ')}</span>
                    </label>
                  ))}
                </div>

                <Button 
                  onClick={handleGenerate}
                  disabled={generateMutation.isPending || selectedSections.length === 0}
                  className="w-full bg-primary text-primary-foreground hover:bg-primary/90"
                >
                  {generateMutation.isPending ? (
                    <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> Generating...</>
                  ) : (
                    'Generate Selected Docs'
                  )}
                </Button>
              </div>

              {docs?.verified !== undefined && (
                <div className="bg-card border border-border rounded-xl p-5 mt-auto">
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
                  >
                    {verifyMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Run Verification Analysis'}
                  </Button>
                </div>
              )}
            </div>

            {/* Viewer area */}
            <div className="lg:col-span-9 bg-card border border-border rounded-xl overflow-hidden flex flex-col h-full">
              <div className="p-4 border-b border-border/50 bg-muted/20 flex justify-between items-center">
                <h3 className="font-medium text-foreground">Documentation Preview</h3>
                <div className="flex items-center gap-2">
                  {docs && <span className="text-xs text-muted-foreground mr-2">Generated {format(new Date(docs.generatedAt), "PP p")}</span>}
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
                {generateMutation.isPending ? (
                  <div className="absolute inset-0 flex flex-col items-center justify-center bg-background/80 backdrop-blur-sm z-10">
                    <RefreshCcw className="w-12 h-12 text-primary animate-spin mb-4" />
                    <h3 className="text-xl font-display font-semibold">AI is writing documentation...</h3>
                    <p className="text-muted-foreground mt-2">Analyzing knowledge graph relationships and generating markdown.</p>
                  </div>
                ) : null}

                {docs ? (
                  <div className="space-y-12">
                    {docs.sections.sort((a,b)=>a.order-b.order).map(section => (
                      <div key={section.slug} id={section.slug}>
                        <MarkdownViewer content={section.content} />
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="h-full flex flex-col items-center justify-center text-center text-muted-foreground">
                    <FileText className="w-16 h-16 mb-4 opacity-20" />
                    <p>No documentation generated yet.</p>
                    <p className="text-sm mt-1">Use the generator panel to create AI documentation.</p>
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

      <div className="border-b border-border">
        <div className="flex overflow-x-auto no-scrollbar gap-6">
          {TABS.map(tab => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`
                  flex items-center gap-2 py-3 border-b-2 transition-colors whitespace-nowrap
                  ${isActive 
                    ? 'border-primary text-primary font-medium' 
                    : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border'
                  }
                `}
              >
                <Icon className="w-4 h-4" />
                {tab.label}
              </button>
            )
          })}
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        {renderTabContent()}
      </div>
    </div>
  );
}
