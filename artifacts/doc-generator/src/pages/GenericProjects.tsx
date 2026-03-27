import { useState, useCallback } from "react";
import { useLocation } from "wouter";
import { useSolutions, useDelete } from "@/hooks/use-solutions";
import { useToast } from "@/hooks/use-toast";
import { UploadDialog } from "@/components/UploadDialog";
import { format } from "date-fns";
import {
  FileText,
  Database,
  Settings,
  Zap,
  Trash2,
  ChevronRight,
  Plus,
  Braces,
  Loader2,
  AlertCircle,
  Download,
  Lock,
  Unlock,
  KeyRound,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";

export default function GenericProjects() {
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const { data: rawSolutions, isLoading, error } = useSolutions("generic");
  const solutions = Array.isArray(rawSolutions) ? rawSolutions : [];
  const deleteMutation = useDelete();
  const [, setLocation] = useLocation();

  const [unlocked, setUnlocked] = useState(false);
  const [showPassGate, setShowPassGate] = useState(false);
  const [passInput, setPassInput] = useState("");
  const [passError, setPassError] = useState(false);
  const UPLOAD_PASS = "Papun@1996";

  const handleUnlock = useCallback(() => {
    if (passInput === UPLOAD_PASS) {
      setUnlocked(true);
      setPassError(false);
      setPassInput("");
      setShowPassGate(false);
      setIsUploadOpen(true);
    } else {
      setPassError(true);
    }
  }, [passInput]);

  const handleNewProject = useCallback(() => {
    if (unlocked) {
      setIsUploadOpen(true);
    } else {
      setShowPassGate(true);
    }
  }, [unlocked]);

  const handleDelete = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (confirm("Delete this project? All generated data will be lost.")) {
      deleteMutation.mutate({ id });
    }
  };

  const { toast } = useToast();

  const handleDownload = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    try {
      const checkRes = await fetch(`/api/py-api/solutions/${id}/download/check`);
      const checkData = await checkRes.json();
      if (!checkData.available) {
        toast({
          title: "Download unavailable",
          description: "The original ZIP file is no longer available.",
          variant: "destructive",
        });
        return;
      }
      const link = document.createElement("a");
      link.href = `/api/py-api/solutions/${id}/download`;
      link.download = "";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch {
      toast({
        title: "Download failed",
        description: "An error occurred while downloading the file.",
        variant: "destructive",
      });
    }
  };

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <h1 className="text-4xl font-display font-bold text-foreground">Other projects</h1>
            <span className="text-xs font-medium uppercase tracking-wider px-2 py-1 rounded-md bg-primary/15 text-primary border border-primary/25">
              PwC Gen AI
            </span>
          </div>
          <p className="text-muted-foreground mt-2 text-lg max-w-2xl">
            Upload non–Microsoft Dynamics source trees (ZIP or GitHub). We index modules and files into
            the same knowledge graph and documentation pipeline powered by PwC Gen AI as Dynamics solutions.
          </p>
        </div>
        <Button
          onClick={handleNewProject}
          className="bg-primary text-primary-foreground hover:bg-primary/90 rounded-xl px-6 py-6 h-auto shadow-lg shadow-primary/20 hover:shadow-xl hover:shadow-primary/30 transition-all hover:-translate-y-0.5"
        >
          {unlocked ? <Plus className="w-5 h-5 mr-2" /> : <Lock className="w-5 h-5 mr-2" />}
          <span className="font-semibold text-base">New project</span>
        </Button>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {[1, 2, 3].map((i) => (
            <div key={i} className="bg-card border border-border rounded-2xl h-64 animate-pulse" />
          ))}
        </div>
      ) : error ? (
        <div className="bg-destructive/10 border border-destructive/20 rounded-2xl p-8 text-center">
          <AlertCircle className="w-10 h-10 text-destructive mx-auto mb-4" />
          <h3 className="text-xl font-semibold text-destructive">Failed to load projects</h3>
          <p className="text-destructive/80 mt-2">Please check the backend connection.</p>
        </div>
      ) : !solutions || solutions.length === 0 ? (
        <div className="bg-card/50 border border-dashed border-border rounded-3xl p-16 text-center flex flex-col items-center justify-center">
          <div className="w-20 h-20 bg-muted rounded-full flex items-center justify-center mb-6">
            <Sparkles className="w-10 h-10 text-muted-foreground" />
          </div>
          <h3 className="text-2xl font-display font-semibold text-foreground">No generic projects yet</h3>
          <p className="text-muted-foreground mt-2 max-w-md">
            Zip your repository (excluding build artifacts where possible) or paste a public GitHub URL. Supported
            languages include Python, TypeScript, JavaScript, Java, Go, C#, Rust, and more.
          </p>
          <Button
            onClick={handleNewProject}
            variant="outline"
            className="mt-8 rounded-xl border-primary/20 text-primary hover:bg-primary/10"
          >
            {unlocked ? <Plus className="w-4 h-4 mr-2" /> : <Lock className="w-4 h-4 mr-2" />}
            Upload or import a project
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
          {solutions.map((sol) => (
            <div
              key={sol.id}
              onClick={() => setLocation(`/solutions/${sol.id}`)}
              className="group bg-card border border-border/60 rounded-2xl p-6 hover:border-violet-500/40 hover:shadow-xl hover:shadow-violet-500/5 transition-all duration-300 cursor-pointer flex flex-col"
            >
              <div className="flex justify-between items-start mb-4">
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-violet-500/20 to-fuchsia-500/20 border border-violet-500/20 flex items-center justify-center">
                  <Braces className="w-6 h-6 text-violet-400" />
                </div>
                <div className="flex items-center gap-2">
                  {sol.status === "processing" && (
                    <span className="flex items-center text-xs font-medium text-amber-500 bg-amber-500/10 px-2.5 py-1 rounded-full border border-amber-500/20">
                      <Loader2 className="w-3 h-3 mr-1 animate-spin" /> Processing
                    </span>
                  )}
                  {sol.status === "error" && (
                    <span className="flex items-center text-xs font-medium text-destructive bg-destructive/10 px-2.5 py-1 rounded-full border border-destructive/20">
                      Failed
                    </span>
                  )}
                  <button
                    onClick={(e) => handleDownload(e, sol.id)}
                    className="p-2 text-muted-foreground hover:text-violet-400 hover:bg-violet-400/10 rounded-lg transition-colors opacity-0 group-hover:opacity-100"
                    title="Download ZIP"
                  >
                    <Download className="w-4 h-4" />
                  </button>
                  <button
                    onClick={(e) => handleDelete(e, sol.id)}
                    className="p-2 text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded-lg transition-colors opacity-0 group-hover:opacity-100"
                    title="Delete project"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>

              <h3 className="text-xl font-semibold text-foreground mb-1 group-hover:text-violet-400 transition-colors line-clamp-1">
                {sol.name}
              </h3>
              <p className="text-sm text-muted-foreground mb-6">
                Uploaded {format(new Date(sol.uploadedAt), "MMM d, yyyy")}
              </p>

              <div className="grid grid-cols-3 gap-2 mb-6 mt-auto">
                <div className="bg-muted/50 rounded-lg p-3 text-center border border-border/50">
                  <div className="text-2xl font-display font-semibold text-foreground">{sol.entityCount}</div>
                  <div className="text-xs text-muted-foreground mt-1 flex items-center justify-center gap-1">
                    <Database className="w-3 h-3" /> Modules
                  </div>
                </div>
                <div className="bg-muted/50 rounded-lg p-3 text-center border border-border/50">
                  <div className="text-2xl font-display font-semibold text-foreground">{sol.workflowCount}</div>
                  <div className="text-xs text-muted-foreground mt-1 flex items-center justify-center gap-1">
                    <Zap className="w-3 h-3" /> Flows
                  </div>
                </div>
                <div className="bg-muted/50 rounded-lg p-3 text-center border border-border/50">
                  <div className="text-2xl font-display font-semibold text-foreground">{sol.pluginCount}</div>
                  <div className="text-xs text-muted-foreground mt-1 flex items-center justify-center gap-1">
                    <Settings className="w-3 h-3" /> Files
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-between pt-4 border-t border-border/50">
                <div className="flex items-center gap-2">
                  {sol.hasDocumentation ? (
                    <span className="flex items-center text-xs font-medium text-emerald-400 bg-emerald-400/10 px-2 py-1 rounded-md border border-emerald-400/20">
                      <FileText className="w-3 h-3 mr-1" /> Docs Ready
                    </span>
                  ) : (
                    <span className="text-xs text-muted-foreground">No docs generated yet</span>
                  )}
                </div>
                <div className="flex items-center text-sm font-medium text-violet-400 group-hover:translate-x-1 transition-transform">
                  Open <ChevronRight className="w-4 h-4 ml-1" />
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {showPassGate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => {
              setShowPassGate(false);
              setPassInput("");
              setPassError(false);
            }}
          />
          <div className="relative bg-card border border-border rounded-2xl p-8 w-full max-w-sm shadow-2xl flex flex-col items-center gap-5 animate-in fade-in zoom-in-95 duration-200">
            <div className="w-16 h-16 rounded-full bg-amber-500/10 border border-amber-500/30 flex items-center justify-center">
              <KeyRound className="w-7 h-7 text-amber-400" />
            </div>
            <div className="text-center">
              <h3 className="text-lg font-semibold text-foreground">Protected Action</h3>
              <p className="text-sm text-muted-foreground mt-1">Enter the password to unlock uploads</p>
            </div>
            <div className="flex items-center gap-2 w-full">
              <input
                type="password"
                autoFocus
                value={passInput}
                onChange={(e) => {
                  setPassInput(e.target.value);
                  setPassError(false);
                }}
                onKeyDown={(e) => e.key === "Enter" && handleUnlock()}
                placeholder="Enter password"
                className={`flex-1 px-3 py-2.5 rounded-lg bg-background border text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 ${passError ? "border-red-500 ring-2 ring-red-500/30" : "border-border"}`}
              />
              <button
                onClick={handleUnlock}
                className="px-4 py-2.5 rounded-lg bg-gradient-to-r from-amber-500 to-orange-600 text-white font-medium text-sm hover:opacity-90 transition-opacity flex items-center gap-1.5"
              >
                <Unlock className="w-4 h-4" /> Unlock
              </button>
            </div>
            {passError && (
              <p className="text-xs text-red-400 flex items-center gap-1">
                <AlertCircle className="w-3 h-3" /> Incorrect password
              </p>
            )}
          </div>
        </div>
      )}

      <UploadDialog open={isUploadOpen} onOpenChange={setIsUploadOpen} processMode="generic" />
    </div>
  );
}
