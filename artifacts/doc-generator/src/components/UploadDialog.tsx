import { useState, useRef, useCallback } from "react";
import type { GitHubImportRequest } from "@workspace/api-client-react";
import { useGitHubImport } from "@/hooks/use-solutions";
import { UploadCloud, X, FileArchive, Loader2, AlertCircle, Github, Link } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { Button } from "@/components/ui/button";
import { useQueryClient } from "@tanstack/react-query";

interface UploadDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** generic = force non-Dynamics source indexing for PwC Gen AI pipeline */
  processMode?: "auto" | "generic";
}

type SourceMode = "zip" | "github";

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function UploadDialog({ open, onOpenChange, processMode = "auto" }: UploadDialogProps) {
  const [mode, setMode] = useState<SourceMode>("zip");
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [githubUrl, setGithubUrl] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const { toast } = useToast();
  const githubMutation = useGitHubImport();
  const queryClient = useQueryClient();

  const isPending = isUploading || githubMutation.isPending;

  if (!open) return null;

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const droppedFile = e.dataTransfer.files[0];
      if (droppedFile.name.endsWith('.zip')) {
        setFile(droppedFile);
        if (!name) setName(droppedFile.name.replace('.zip', ''));
      } else {
        toast({
          title: "Invalid file type",
          description:
            processMode === "generic"
              ? "Please upload a project .zip archive."
              : "Please upload a Dynamics Solution .zip file.",
          variant: "destructive"
        });
      }
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const selectedFile = e.target.files[0];
      setFile(selectedFile);
      if (!name) setName(selectedFile.name.replace('.zip', ''));
    }
  };

  const handleClose = () => {
    if (xhrRef.current) {
      xhrRef.current.abort();
      xhrRef.current = null;
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    onOpenChange(false);
    setFile(null);
    setName("");
    setGithubUrl("");
    setMode("zip");
    setUploadProgress(null);
    setIsUploading(false);
  };

  const CHUNK_SIZE = 50 * 1024 * 1024; // 50 MB per chunk

  const uploadWithProgress = async (fileToUpload: File, solutionName: string) => {
    setIsUploading(true);
    setUploadProgress(0);

    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    const totalSize = fileToUpload.size;
    const totalChunks = Math.ceil(totalSize / CHUNK_SIZE);

    try {
      const initRes = await fetch("/api/py-api/solutions/upload/init", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: fileToUpload.name,
          totalSize,
          totalChunks,
          name: solutionName,
          processMode,
        }),
        signal: abortController.signal,
      });

      if (!initRes.ok) {
        const err = await initRes.json().catch(() => ({ detail: "Failed to start upload" }));
        throw new Error(err.detail || "Failed to start upload");
      }

      const { uploadId } = await initRes.json();

      for (let i = 0; i < totalChunks; i++) {
        if (abortController.signal.aborted) return;

        const start = i * CHUNK_SIZE;
        const end = Math.min(start + CHUNK_SIZE, totalSize);
        const chunkBlob = fileToUpload.slice(start, end);

        const chunkForm = new FormData();
        chunkForm.append("file", chunkBlob, fileToUpload.name);
        chunkForm.append("uploadId", uploadId);
        chunkForm.append("chunkIndex", String(i));

        let retries = 0;
        const maxRetries = 3;
        while (retries <= maxRetries) {
          try {
            const chunkRes = await fetch("/api/py-api/solutions/upload/chunk", {
              method: "POST",
              body: chunkForm,
              signal: abortController.signal,
            });

            if (!chunkRes.ok) {
              const err = await chunkRes.json().catch(() => ({ detail: "Chunk upload failed" }));
              throw new Error(err.detail || "Chunk upload failed");
            }
            break;
          } catch (err: any) {
            if (err.name === "AbortError") return;
            retries++;
            if (retries > maxRetries) throw err;
            await new Promise((r) => setTimeout(r, 1000 * retries));
          }
        }

        const percent = Math.round(((i + 1) / totalChunks) * 100);
        setUploadProgress(percent);
      }

      const finalRes = await fetch("/api/py-api/solutions/upload/finalize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uploadId, name: solutionName }),
        signal: abortController.signal,
      });

      if (!finalRes.ok) {
        const err = await finalRes.json().catch(() => ({ detail: "Finalize failed" }));
        throw new Error(err.detail || "Finalize failed");
      }

      abortControllerRef.current = null;
      setIsUploading(false);
      setUploadProgress(null);
      queryClient.invalidateQueries({
        predicate: (q) =>
          Array.isArray(q.queryKey) && q.queryKey[0] === "/api/py-api/solutions",
      });
      toast({ title: "Upload complete", description: "Solution is now being parsed and processed." });
      handleClose();
    } catch (err: any) {
      if (err.name === "AbortError") {
        abortControllerRef.current = null;
        setIsUploading(false);
        setUploadProgress(null);
        return;
      }
      abortControllerRef.current = null;
      setIsUploading(false);
      setUploadProgress(null);
      toast({
        title: "Upload failed",
        description: err.message || "An unexpected error occurred.",
        variant: "destructive",
      });
    }
  };

  const handleSubmit = () => {
    if (mode === "zip") {
      if (!file) return;
      uploadWithProgress(file, name || file.name);
    } else {
      if (!githubUrl.trim()) return;
      const body: GitHubImportRequest = {
        url: githubUrl.trim(),
        name: name || "",
        processMode,
      };
      githubMutation.mutate(
        { data: body },
        {
          onSuccess: () => {
            toast({ title: "Import started", description: "GitHub repository is being downloaded and processed." });
            handleClose();
          },
          onError: (error) => {
            toast({ title: "Import failed", description: error.data?.error || "Failed to import from GitHub.", variant: "destructive" });
          }
        }
      );
    }
  };

  const canSubmit = mode === "zip" ? !!file : !!githubUrl.trim();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-card border border-border rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden animate-in zoom-in-95 duration-200">
        <div className="flex justify-between items-center p-6 border-b border-border/50">
          <h2 className="text-xl font-display font-semibold">
            {processMode === "generic" ? "Import project" : "Import Solution"}
          </h2>
          <button 
            onClick={handleClose}
            className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded-md hover:bg-muted"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-6">
          <div className="flex gap-2 p-1 bg-muted/50 rounded-xl border border-border/50">
            <button
              onClick={() => setMode("zip")}
              disabled={isPending}
              className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-all ${
                mode === "zip"
                  ? "bg-background text-foreground shadow-sm border border-border/50"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <UploadCloud className="w-4 h-4" />
              Upload ZIP
            </button>
            <button
              onClick={() => setMode("github")}
              disabled={isPending}
              className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-all ${
                mode === "github"
                  ? "bg-background text-foreground shadow-sm border border-border/50"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Github className="w-4 h-4" />
              GitHub URL
            </button>
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">Solution Name (Optional)</label>
            <input 
              type="text" 
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Core CRM Customizations"
              disabled={isPending}
              className="w-full px-4 py-2.5 rounded-xl bg-background border border-border text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 transition-all disabled:opacity-50"
            />
          </div>

          {mode === "zip" ? (
            <>
              <div 
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={isUploading ? undefined : handleDrop}
                className={`
                  border-2 border-dashed rounded-xl p-8 text-center transition-all duration-200
                  ${isUploading ? 'cursor-default' : 'cursor-pointer'}
                  ${isDragging ? 'border-primary bg-primary/5' : 'border-border hover:border-muted-foreground/50 hover:bg-muted/20'}
                  ${file ? 'bg-muted/30 border-solid border-muted-foreground/30' : ''}
                `}
                onClick={() => !file && !isUploading && fileInputRef.current?.click()}
              >
                <input 
                  type="file" 
                  accept=".zip"
                  className="hidden" 
                  ref={fileInputRef}
                  onChange={handleFileSelect}
                />
                
                {file ? (
                  <div className="flex flex-col items-center gap-3">
                    <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center text-primary">
                      <FileArchive className="w-6 h-6" />
                    </div>
                    <div>
                      <p className="font-medium text-foreground">{file.name}</p>
                      <p className="text-xs text-muted-foreground">{formatFileSize(file.size)}</p>
                    </div>
                    {!isUploading && (
                      <button 
                        onClick={(e) => { e.stopPropagation(); setFile(null); }}
                        className="mt-2 text-sm text-destructive hover:underline"
                      >
                        Remove file
                      </button>
                    )}
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-3">
                    <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center text-muted-foreground">
                      <UploadCloud className="w-6 h-6" />
                    </div>
                    <div>
                      <p className="font-medium text-foreground">Click to upload or drag and drop</p>
                      <p className="text-sm text-muted-foreground mt-1">
                        {processMode === "generic"
                          ? "Project source ZIP (Python, TypeScript, Java, Go, etc.) — up to 10 GB"
                          : "Dynamics Solution ZIP files (up to 10 GB)"}
                      </p>
                    </div>
                  </div>
                )}
              </div>

              {uploadProgress !== null && (
                <div className="space-y-2">
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">
                      {uploadProgress < 100 ? "Uploading..." : "Processing..."}
                    </span>
                    <span className="font-medium text-foreground">{uploadProgress}%</span>
                  </div>
                  <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
                    <div
                      className="h-full bg-primary rounded-full transition-all duration-300 ease-out"
                      style={{ width: `${uploadProgress}%` }}
                    />
                  </div>
                  {file && uploadProgress < 100 && (
                    <p className="text-xs text-muted-foreground text-center">
                      Large files may take several minutes to upload
                    </p>
                  )}
                </div>
              )}
            </>
          ) : (
            <div className="space-y-3">
              <div className="space-y-2">
                <label className="text-sm font-medium text-foreground">GitHub Repository URL</label>
                <div className="relative">
                  <Link className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <input 
                    type="url"
                    value={githubUrl}
                    onChange={(e) => setGithubUrl(e.target.value)}
                    placeholder="https://github.com/owner/repo"
                    disabled={isPending}
                    className="w-full pl-10 pr-4 py-2.5 rounded-xl bg-background border border-border text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 transition-all disabled:opacity-50"
                  />
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                The repository must be public. The main or master branch will be downloaded automatically.
              </p>
            </div>
          )}
          
          <div className="flex items-start gap-2 p-3 rounded-lg bg-blue-500/10 border border-blue-500/20 text-blue-400 text-sm">
            <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
            <p>
              {processMode === "generic"
                ? mode === "zip"
                  ? "Files are indexed into a knowledge graph; PwC Gen AI then generates the same structured documentation as Dynamics projects."
                  : "The repository is downloaded and indexed for PwC Gen AI documentation (non-Dynamics pipeline)."
                : mode === "zip"
                  ? "The solution will be unpacked, parsed, and converted into a Knowledge Graph automatically."
                  : "The repository will be downloaded, scanned for Dynamics solution files, and converted into a Knowledge Graph."}
            </p>
          </div>
        </div>

        <div className="p-6 bg-muted/30 border-t border-border/50 flex justify-end gap-3">
          <Button 
            variant="outline" 
            onClick={handleClose}
            disabled={githubMutation.isPending}
            className="rounded-xl px-5"
          >
            {isUploading ? "Cancel Upload" : "Cancel"}
          </Button>
          <Button 
            onClick={handleSubmit}
            disabled={!canSubmit || isPending}
            className="rounded-xl px-6 bg-primary text-primary-foreground hover:bg-primary/90 shadow-lg shadow-primary/20"
          >
            {isPending ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                {isUploading ? "Uploading..." : mode === "github" ? "Importing..." : "Processing..."}
              </>
            ) : (
              mode === "github" ? "Import & Process" : "Upload & Process"
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
