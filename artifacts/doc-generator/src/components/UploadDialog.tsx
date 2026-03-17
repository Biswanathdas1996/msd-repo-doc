import { useState, useRef } from "react";
import { useUpload, useGitHubImport } from "@/hooks/use-solutions";
import { UploadCloud, X, FileArchive, Loader2, AlertCircle, Github, Link } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { Button } from "@/components/ui/button";

interface UploadDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type SourceMode = "zip" | "github";

export function UploadDialog({ open, onOpenChange }: UploadDialogProps) {
  const [mode, setMode] = useState<SourceMode>("zip");
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [githubUrl, setGithubUrl] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { toast } = useToast();
  const uploadMutation = useUpload();
  const githubMutation = useGitHubImport();

  const isPending = uploadMutation.isPending || githubMutation.isPending;

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
          description: "Please upload a Dynamics Solution .zip file.",
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
    onOpenChange(false);
    setFile(null);
    setName("");
    setGithubUrl("");
    setMode("zip");
  };

  const handleSubmit = () => {
    if (mode === "zip") {
      if (!file) return;
      uploadMutation.mutate(
        { data: { file, name: name || file.name } },
        {
          onSuccess: () => {
            toast({ title: "Upload started", description: "Solution is now being parsed and processed." });
            handleClose();
          },
          onError: (error) => {
            toast({ title: "Upload failed", description: error.data?.error || "An unexpected error occurred.", variant: "destructive" });
          }
        }
      );
    } else {
      if (!githubUrl.trim()) return;
      githubMutation.mutate(
        { data: { url: githubUrl.trim(), name: name || "" } },
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
          <h2 className="text-xl font-display font-semibold">Import Solution</h2>
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
              className="w-full px-4 py-2.5 rounded-xl bg-background border border-border text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 transition-all"
            />
          </div>

          {mode === "zip" ? (
            <div 
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`
                border-2 border-dashed rounded-xl p-8 text-center transition-all duration-200 cursor-pointer
                ${isDragging ? 'border-primary bg-primary/5' : 'border-border hover:border-muted-foreground/50 hover:bg-muted/20'}
                ${file ? 'bg-muted/30 border-solid border-muted-foreground/30' : ''}
              `}
              onClick={() => !file && fileInputRef.current?.click()}
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
                    <p className="text-xs text-muted-foreground">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
                  </div>
                  <button 
                    onClick={(e) => { e.stopPropagation(); setFile(null); }}
                    className="mt-2 text-sm text-destructive hover:underline"
                  >
                    Remove file
                  </button>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-3">
                  <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center text-muted-foreground">
                    <UploadCloud className="w-6 h-6" />
                  </div>
                  <div>
                    <p className="font-medium text-foreground">Click to upload or drag and drop</p>
                    <p className="text-sm text-muted-foreground mt-1">Dynamics Solution ZIP files only</p>
                  </div>
                </div>
              )}
            </div>
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
                    className="w-full pl-10 pr-4 py-2.5 rounded-xl bg-background border border-border text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/50 transition-all"
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
              {mode === "zip" 
                ? "The solution will be unpacked, parsed, and converted into a Knowledge Graph automatically."
                : "The repository will be downloaded, scanned for Dynamics solution files, and converted into a Knowledge Graph."
              }
            </p>
          </div>
        </div>

        <div className="p-6 bg-muted/30 border-t border-border/50 flex justify-end gap-3">
          <Button 
            variant="outline" 
            onClick={handleClose}
            disabled={isPending}
            className="rounded-xl px-5"
          >
            Cancel
          </Button>
          <Button 
            onClick={handleSubmit}
            disabled={!canSubmit || isPending}
            className="rounded-xl px-6 bg-primary text-primary-foreground hover:bg-primary/90 shadow-lg shadow-primary/20"
          >
            {isPending ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                {mode === "github" ? "Importing..." : "Processing..."}
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
