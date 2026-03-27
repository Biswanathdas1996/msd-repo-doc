import { useState, useCallback, useEffect, useRef, memo, type RefObject } from "react";
import {
  useAdvancedDocsList,
  useAdvancedDoc,
  useAdvancedDocUpload,
  useAdvancedDocStream,
  useDeleteAdvancedDoc,
  useImportAdvancedDoc,
  useRegenerateAdvancedTechnicalSpecs,
  exportAdvancedDoc,
  type AdvancedDocResult,
  type TechnicalSpecs,
  type StreamStep,
  type StepStatus,
  type QualityScore,
} from "@/hooks/use-advanced-docs";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import mermaid from "mermaid";
import {
  Upload,
  FileCode2,
  Network,
  ListTree,
  Link2,
  GitBranch,
  FileText,
  Trash2,
  Loader2,
  ArrowLeft,
  ChevronRight,
  AlertCircle,
  CheckCircle2,
  FolderTree,
  Sparkles,
  Download,
  FileUp,
  Image,
  SkipForward,
  CircleDot,
  ShieldCheck,
  BarChart3,
  Lock,
  Unlock,
  KeyRound,
  Maximize2,
  X,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  ClipboardList,
  Shield,
  Database,
  Layers,
  Workflow,
  Code2,
  Plug,
} from "lucide-react";

// ─── Mermaid rendering ───────────────────────────────────────────────────────

mermaid.initialize({
  startOnLoad: false,
  theme: "dark",
  securityLevel: "loose",
  themeVariables: {
    fontSize: "15px",
    fontFamily: "ui-sans-serif, system-ui, sans-serif",
    primaryTextColor: "#fafafa",
    secondaryTextColor: "#e4e4e7",
    tertiaryTextColor: "#a1a1aa",
    lineColor: "#a1a1aa",
    primaryBorderColor: "#52525b",
    /* Avoid default light node fills (#ccf, etc.) on flowcharts */
    primaryColor: "#1e293b",
    secondaryColor: "#334155",
    tertiaryColor: "#1e293b",
    mainBkg: "#0f172a",
    nodeBorder: "#52525b",
  },
});

/** Use viewBox pixel size as width/height so wide ER diagrams are not squashed to 100% (tiny text). */
function useNativeSvgSizing(svgHtml: string): string {
  const m = svgHtml.match(
    /viewBox="\s*([\d.+-]+)\s+([\d.+-]+)\s+([\d.]+)\s+([\d.]+)\s*"/i,
  );
  if (!m) return svgHtml;
  const vw = m[3];
  const vh = m[4];
  return svgHtml.replace(/<svg\b([^>]*)>/i, (_full, attrs: string) => {
    let a = attrs;
    if (/\bwidth\s*=/i.test(a)) a = a.replace(/\swidth\s*=\s*"[^"]*"/i, ` width="${vw}"`);
    else a = ` width="${vw}"${a}`;
    if (/\bheight\s*=/i.test(a)) a = a.replace(/\sheight\s*=\s*"[^"]*"/i, ` height="${vh}"`);
    else a = ` height="${vh}"${a}`;
    if (/\bstyle\s*=/i.test(a)) {
      a = a.replace(/\sstyle\s*=\s*"/i, ' style="max-width:none;');
    } else {
      a += ` style="max-width:none"`;
    }
    return `<svg${a}>`;
  });
}

/**
 * Prepare an export-ready SVG string from the Mermaid-rendered SVG.
 * - Adds a solid dark background rect so it's readable in any viewer
 * - Inlines the foreignObject HTML styles so text renders outside the browser
 * - Returns { svgString, width, height }
 */
function prepareExportSvg(rawSvg: string): { svgString: string; width: number; height: number } {
  const parser = new DOMParser();
  const doc = parser.parseFromString(rawSvg, "image/svg+xml");
  const svgEl = doc.documentElement;

  const width = parseFloat(svgEl.getAttribute("width") || "800");
  const height = parseFloat(svgEl.getAttribute("height") || "600");

  // Ensure SVG has explicit xmlns for standalone use
  svgEl.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  svgEl.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");
  svgEl.setAttribute("xmlns:xhtml", "http://www.w3.org/1999/xhtml");

  // Insert a background rect as the very first child
  const bgRect = doc.createElementNS("http://www.w3.org/2000/svg", "rect");
  bgRect.setAttribute("width", "100%");
  bgRect.setAttribute("height", "100%");
  bgRect.setAttribute("fill", "#0f1117");
  svgEl.insertBefore(bgRect, svgEl.firstChild);

  // Inline styles on foreignObject HTML so they render in external viewers
  const foDivs = svgEl.querySelectorAll("foreignObject div, foreignObject span, foreignObject p");
  foDivs.forEach((el) => {
    const htmlEl = el as HTMLElement;
    if (!htmlEl.style.color) htmlEl.style.color = "#f1f5f9";
    if (!htmlEl.style.fontFamily) htmlEl.style.fontFamily = "Inter, sans-serif";
    if (!htmlEl.style.fontSize) htmlEl.style.fontSize = "13px";
    if (!htmlEl.style.lineHeight) htmlEl.style.lineHeight = "1.4";
    if (!htmlEl.style.textAlign) htmlEl.style.textAlign = "center";
  });

  const serializer = new XMLSerializer();
  return { svgString: serializer.serializeToString(svgEl), width, height };
}

function downloadSvg(svgContent: string, filename: string) {
  const { svgString } = prepareExportSvg(svgContent);
  const blob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${filename}.svg`;
  a.click();
  URL.revokeObjectURL(url);
}

function downloadPng(svgContent: string, filename: string) {
  const { svgString, width, height } = prepareExportSvg(svgContent);
  const scale = 2;

  // Replace foreignObject with simple SVG text for canvas compatibility.
  // Canvas taints when drawing SVGs containing foreignObject (CORS).
  const parser = new DOMParser();
  const doc = parser.parseFromString(svgString, "image/svg+xml");
  const svgEl = doc.documentElement;

  const foreignObjects = svgEl.querySelectorAll("foreignObject");
  foreignObjects.forEach((fo) => {
    const textContent = fo.textContent?.trim() || "";
    // Get the transform-corrected position from the parent group
    const parent = fo.parentElement;
    const transform = parent?.getAttribute("transform") || "";
    const translateMatch = transform.match(/translate\(\s*([\d.-]+)[,\s]+([\d.-]+)\)/);

    const foX = parseFloat(fo.getAttribute("x") || "0");
    const foY = parseFloat(fo.getAttribute("y") || "0");
    const foW = parseFloat(fo.getAttribute("width") || "100");
    const foH = parseFloat(fo.getAttribute("height") || "20");

    const textEl = doc.createElementNS("http://www.w3.org/2000/svg", "text");

    if (translateMatch) {
      // Position text at center of foreignObject within the translated group
      textEl.setAttribute("x", String(foX + foW / 2));
      textEl.setAttribute("y", String(foY + foH / 2));
    } else {
      textEl.setAttribute("x", String(foX + foW / 2));
      textEl.setAttribute("y", String(foY + foH / 2));
    }

    textEl.setAttribute("text-anchor", "middle");
    textEl.setAttribute("dominant-baseline", "central");
    textEl.setAttribute("font-family", "Inter, Arial, sans-serif");
    textEl.setAttribute("font-size", "13");
    textEl.setAttribute("font-weight", "500");
    textEl.setAttribute("fill", "#f1f5f9");

    // Truncate long labels
    textEl.textContent = textContent.length > 50 ? textContent.slice(0, 47) + "..." : textContent;

    fo.parentNode?.replaceChild(textEl, fo);
  });

  const serializer = new XMLSerializer();
  const pngSvg = serializer.serializeToString(svgEl);
  const dataUrl = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(pngSvg);

  const canvas = document.createElement("canvas");
  canvas.width = width * scale;
  canvas.height = height * scale;
  const ctx = canvas.getContext("2d")!;
  ctx.scale(scale, scale);

  const img = new window.Image();
  img.onload = () => {
    // Background already baked into the SVG via prepareExportSvg
    ctx.drawImage(img, 0, 0, width, height);

    const a = document.createElement("a");
    a.href = canvas.toDataURL("image/png");
    a.download = `${filename}.png`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };
  img.onerror = () => {
    // Fallback: download the SVG instead
    downloadSvg(svgContent, filename);
  };
  img.src = dataUrl;
}

function DiagramFullscreenViewer({ svgHtml, title, onClose }: { svgHtml: string; title?: string; onClose: () => void }) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });
  const [viewportSize, setViewportSize] = useState({ w: 0, h: 0 });
  const [contentSize, setContentSize] = useState({ w: 0, h: 0 });
  const dragRef = useRef<{ dragging: boolean; startX: number; startY: number; origX: number; origY: number }>({
    dragging: false, startX: 0, startY: 0, origX: 0, origY: 0,
  });

  useEffect(() => {
    if (viewportRef.current) {
      setViewportSize({ w: viewportRef.current.clientWidth, h: viewportRef.current.clientHeight });
    }
    if (contentRef.current) {
      const svg = contentRef.current.querySelector("svg");
      if (svg) setContentSize({ w: svg.clientWidth || 800, h: svg.clientHeight || 600 });
    }
  }, [svgHtml]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY < 0 ? 0.12 : -0.12;
    setScale(prev => Math.min(6, Math.max(0.08, prev * (1 + delta))));
  }, []);

  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    dragRef.current = { dragging: true, startX: e.clientX, startY: e.clientY, origX: translate.x, origY: translate.y };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }, [translate]);

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragRef.current.dragging) return;
    setTranslate({
      x: dragRef.current.origX + (e.clientX - dragRef.current.startX),
      y: dragRef.current.origY + (e.clientY - dragRef.current.startY),
    });
  }, []);

  const handlePointerUp = useCallback(() => { dragRef.current.dragging = false; }, []);

  const resetView = useCallback(() => { setScale(1); setTranslate({ x: 0, y: 0 }); }, []);
  const fitToScreen = useCallback(() => {
    if (contentSize.w > 0 && contentSize.h > 0 && viewportSize.w > 0) {
      const fitScale = Math.min(viewportSize.w / contentSize.w, viewportSize.h / contentSize.h) * 0.92;
      setScale(fitScale);
      setTranslate({ x: 0, y: 0 });
    }
  }, [contentSize, viewportSize]);
  const zoomIn = useCallback(() => setScale(prev => Math.min(6, prev * 1.3)), []);
  const zoomOut = useCallback(() => setScale(prev => Math.max(0.08, prev / 1.3)), []);

  const scalePercent = Math.round(scale * 100);

  return (
    <div className="fixed inset-0 z-[9999] flex flex-col bg-[#070a12] animate-in fade-in duration-200">
      {/* Toolbar */}
      <div className="relative flex items-center justify-between px-5 h-14 border-b border-white/[0.06] bg-gradient-to-r from-[#0d1117]/95 via-[#101824]/95 to-[#0d1117]/95 backdrop-blur-xl">
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-primary/10 border border-primary/20">
            <Network className="w-4 h-4 text-primary" />
          </div>
          <div>
            <span className="text-sm font-semibold text-foreground tracking-tight">{title || "Diagram"}</span>
            <span className="text-[10px] text-muted-foreground ml-2 hidden sm:inline">Interactive Viewer</span>
          </div>
        </div>

        {/* Zoom controls */}
        <div className="absolute left-1/2 -translate-x-1/2 flex items-center gap-0.5 bg-white/[0.04] border border-white/[0.06] rounded-full px-1 py-0.5">
          <button onClick={zoomOut} className="p-1.5 rounded-full hover:bg-white/[0.08] text-muted-foreground hover:text-foreground transition-colors" title="Zoom out">
            <ZoomOut className="w-3.5 h-3.5" />
          </button>
          <button onClick={resetView} className="px-2 py-1 rounded-full hover:bg-white/[0.08] transition-colors group min-w-[52px]" title="Reset to 100%">
            <span className="text-[11px] font-medium tabular-nums text-muted-foreground group-hover:text-foreground transition-colors">{scalePercent}%</span>
          </button>
          <button onClick={zoomIn} className="p-1.5 rounded-full hover:bg-white/[0.08] text-muted-foreground hover:text-foreground transition-colors" title="Zoom in">
            <ZoomIn className="w-3.5 h-3.5" />
          </button>
          <div className="w-px h-4 bg-white/[0.08] mx-0.5" />
          <button onClick={fitToScreen} className="px-2 py-1 rounded-full hover:bg-white/[0.08] text-muted-foreground hover:text-foreground transition-colors text-[11px] font-medium" title="Fit to screen">
            Fit
          </button>
          <button onClick={resetView} className="p-1.5 rounded-full hover:bg-white/[0.08] text-muted-foreground hover:text-foreground transition-colors" title="Reset view">
            <RotateCcw className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Right: download + close */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => downloadSvg(svgHtml, "diagram")}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-500/10 border border-blue-500/20 text-blue-300 hover:text-blue-200 hover:bg-blue-500/20 transition-all text-xs font-medium"
            title="Download SVG"
          >
            <Download className="w-3.5 h-3.5" /> SVG
          </button>
          <button
            onClick={() => downloadPng(svgHtml, "diagram")}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-500/10 border border-blue-500/20 text-blue-300 hover:text-blue-200 hover:bg-blue-500/20 transition-all text-xs font-medium"
            title="Download PNG"
          >
            <Image className="w-3.5 h-3.5" /> PNG
          </button>
          <button onClick={onClose} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg hover:bg-red-500/10 border border-transparent hover:border-red-500/20 text-muted-foreground hover:text-red-400 transition-all text-xs font-medium" title="Close (Esc)">
            <X className="w-4 h-4" />
            <span className="hidden sm:inline">Close</span>
          </button>
        </div>
      </div>

      {/* Canvas */}
      <div
        ref={viewportRef}
        className="relative flex-1 overflow-hidden cursor-grab active:cursor-grabbing select-none"
        style={{ background: "radial-gradient(circle at 50% 50%, #0d1420 0%, #070a12 100%)" }}
        onWheel={handleWheel}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      >
        <div className="absolute inset-0 opacity-[0.035]" style={{
          backgroundImage: "radial-gradient(circle, #94a3b8 1px, transparent 1px)",
          backgroundSize: "28px 28px",
        }} />
        <div
          ref={contentRef}
          className="w-full h-full flex items-center justify-center"
          style={{
            transform: `translate(${translate.x}px, ${translate.y}px) scale(${scale})`,
            transformOrigin: "center center",
            transition: dragRef.current.dragging ? "none" : "transform 0.18s cubic-bezier(.22,1,.36,1)",
          }}
        >
          <div
            className="mermaid-container [&_svg]:max-w-none [&_svg]:max-h-none [&_svg]:drop-shadow-lg"
            dangerouslySetInnerHTML={{ __html: svgHtml }}
          />
        </div>
      </div>

      {/* Footer hints */}
      <div className="flex items-center justify-center gap-6 px-5 h-9 border-t border-white/[0.04] bg-[#0a0e17]/80">
        <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground/70">
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M15 15l-2 5L9 9l11 4-5 2zm0 0l5 5M7.188 2.239l.777 2.897M5.136 7.965l-2.898-.777M13.95 4.05l-2.122 2.122m-5.657 5.656l-2.12 2.122" /></svg>
          Scroll to zoom
        </span>
        <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground/70">
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M7 11.5V14m0 0v2.5m0-2.5h2.5M7 14H4.5m4-5.5l-.757-.757A2 2 0 006.172 7H4a1 1 0 00-1 1v1.172a2 2 0 00.586 1.414l8.828 8.828a2 2 0 002.828 0l2.172-2.172a2 2 0 000-2.828L8.586 5.586A2 2 0 007.172 5H7v3.5z" /></svg>
          Drag to pan
        </span>
        <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground/70">
          <kbd className="px-1 py-0.5 rounded bg-white/[0.06] border border-white/[0.08] text-[10px] font-mono leading-none">Esc</kbd>
          Close
        </span>
      </div>
    </div>
  );
}

const MermaidDiagram = memo(
  ({
    chart,
    id,
    variant = "default",
  }: {
    chart: string;
    id: string;
    /** `wide`: native SVG pixel size + scroll (readable ERDs); default keeps Mermaid width="100%" fit. */
    variant?: "default" | "wide";
  }) => {
  const ref = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        let { svg: rendered } = await mermaid.render(`mermaid-${id}`, chart);
        if (variant === "wide") {
          rendered = useNativeSvgSizing(rendered);
        }
        if (!cancelled) setSvg(rendered);
      } catch (e: any) {
        if (!cancelled) setError(e?.message || "Failed to render diagram");
      }
    })();
    return () => { cancelled = true; };
  }, [chart, id, variant]);

  if (error) {
    return (
      <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-sm">
        <p className="font-medium mb-1">Diagram render error</p>
        <pre className="text-xs overflow-auto">{chart}</pre>
      </div>
    );
  }

  return svg ? (
    <>
      <div className="relative group">
        <div className="absolute top-2 right-2 flex gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity z-10">
          <button
            onClick={() => setFullscreen(true)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-background/90 border border-border/50 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-background transition-colors backdrop-blur-sm shadow-sm"
            title="Open fullscreen"
          >
            <Maximize2 className="w-3.5 h-3.5" /> Fullscreen
          </button>
          <button
            onClick={() => downloadSvg(svg, id)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-background/90 border border-border/50 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-background transition-colors backdrop-blur-sm shadow-sm"
            title="Download SVG"
          >
            <Download className="w-3.5 h-3.5" /> SVG
          </button>
          <button
            onClick={() => downloadPng(svg, id)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-background/90 border border-border/50 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-background transition-colors backdrop-blur-sm shadow-sm"
            title="Download PNG"
          >
            <Image className="w-3.5 h-3.5" /> PNG
          </button>
        </div>
        <div
          ref={ref}
          className={
            variant === "wide"
              ? "overflow-auto max-h-[min(85vh,920px)] min-h-[280px] rounded-xl border-2 border-primary/35 bg-muted/45 p-4 sm:p-5 shadow-inner ring-1 ring-border/60 [&::-webkit-scrollbar]:h-2.5 [&::-webkit-scrollbar]:w-2.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-border"
              : "overflow-auto bg-card/50 rounded-xl p-4 border border-border/50"
          }
        >
          {variant === "wide" && (
            <p className="text-xs text-muted-foreground mb-3 flex flex-wrap items-center gap-2">
              <Maximize2 className="w-3.5 h-3.5 shrink-0 text-primary/80" />
              <span>
                Diagram shown at full resolution — scroll horizontally and vertically. Open{" "}
                <strong className="text-foreground/90">Fullscreen</strong> for the clearest view.
              </span>
            </p>
          )}
          <div
            className={
              variant === "wide"
                ? "mermaid-container inline-block min-w-min rounded-lg bg-background/40 p-2 ring-1 ring-border/40"
                : "mermaid-container"
            }
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        </div>
      </div>
      {fullscreen && (
        <DiagramFullscreenViewer
          svgHtml={svg}
          title={id.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase())}
          onClose={() => setFullscreen(false)}
        />
      )}
    </>
  ) : (
    <div className="flex items-center gap-2 text-muted-foreground text-sm py-4">
      <Loader2 className="w-4 h-4 animate-spin" /> Rendering diagram...
    </div>
  );
});

// ─── Markdown viewer with Mermaid support ────────────────────────────────────

function DocMarkdown({ content, containerRef }: { content: string; containerRef?: RefObject<HTMLDivElement | null> }) {
  const idCounter = useRef(0);
  // Reset counter on each render so mermaid IDs are stable
  idCounter.current = 0;

  return (
    <div ref={containerRef} className="doc-markdown space-y-4 text-sm text-foreground leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h1 className="text-2xl font-bold mt-8 mb-4 text-foreground">{children}</h1>,
          h2: ({ children }) => <h2 className="text-xl font-semibold mt-6 mb-3 text-foreground border-b border-border/50 pb-2">{children}</h2>,
          h3: ({ children }) => <h3 className="text-lg font-semibold mt-5 mb-2 text-foreground">{children}</h3>,
          h4: ({ children }) => <h4 className="text-base font-semibold mt-4 mb-2 text-foreground">{children}</h4>,
          p: ({ children }) => <p className="my-2 text-muted-foreground">{children}</p>,
          ul: ({ children }) => <ul className="list-disc pl-6 my-2 space-y-1 text-muted-foreground">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal pl-6 my-2 space-y-1 text-muted-foreground">{children}</ol>,
          li: ({ children }) => <li className="text-muted-foreground">{children}</li>,
          a: ({ href, children }) => <a href={href} className="text-primary hover:underline">{children}</a>,
          strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
          blockquote: ({ children }) => <blockquote className="border-l-4 border-primary/30 pl-4 my-3 italic text-muted-foreground">{children}</blockquote>,
          table: ({ children }) => (
            <div className="overflow-auto my-4 rounded-lg border border-border/50">
              <table className="w-full text-sm">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-muted/50 border-b border-border/50">{children}</thead>,
          th: ({ children }) => <th className="text-left p-3 font-medium text-foreground">{children}</th>,
          td: ({ children }) => <td className="p-3 border-t border-border/30 text-muted-foreground">{children}</td>,
          hr: () => <hr className="border-border/50 my-6" />,
          pre({ children }) {
            return <div className="my-4">{children}</div>;
          },
          code({ className, children, ...props }) {
            const lang = className?.replace("language-", "") || "";
            const content = String(children).replace(/\n$/, "");
            if (lang === "mermaid") {
              const diagId = `doc-${idCounter.current++}`;
              return <MermaidDiagram chart={content} id={diagId} />;
            }
            // Block code: has language class OR contains newlines
            if (className || content.includes("\n")) {
              return (
                <pre className="bg-card/50 border border-border/50 rounded-xl p-4 overflow-auto text-xs font-mono text-muted-foreground whitespace-pre">
                  <code>{children}</code>
                </pre>
              );
            }
            // Inline code
            return (
              <code className="bg-muted px-1.5 py-0.5 rounded text-xs font-mono text-foreground" {...props}>
                {children}
              </code>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

// ─── Tabs ────────────────────────────────────────────────────────────────────

type TabKey = "overview" | "knowledge-graph" | "features" | "connections" | "flows" | "technical-specs" | "documentation";

const TABS: { key: TabKey; label: string; icon: typeof FileCode2 }[] = [
  { key: "overview", label: "Overview", icon: FolderTree },
  { key: "knowledge-graph", label: "Knowledge Graph", icon: Network },
  { key: "features", label: "Features", icon: ListTree },
  { key: "connections", label: "Feature Connections", icon: Link2 },
  { key: "flows", label: "Flow Diagrams", icon: GitBranch },
  { key: "technical-specs", label: "Technical Specs", icon: ClipboardList },
  { key: "documentation", label: "Documentation", icon: FileText },
];

// ─── Overview Tab ────────────────────────────────────────────────────────────

function QualityBadge({ score }: { score?: QualityScore }) {
  if (!score) return null;
  const val = score.overall_score;
  const color = val >= 90 ? "text-green-400 bg-green-500/10 border-green-500/30"
    : val >= 70 ? "text-yellow-400 bg-yellow-500/10 border-yellow-500/30"
    : "text-red-400 bg-red-500/10 border-red-500/30";
  return (
    <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-sm font-semibold ${color}`}>
      <BarChart3 className="w-4 h-4" />
      Quality: {val.toFixed(0)}%
      {score.meets_target && <CheckCircle2 className="w-3.5 h-3.5" />}
    </div>
  );
}

function OverviewTab({ data }: { data: AdvancedDocResult }) {
  const quality = data.quality_score;
  return (
    <div className="space-y-6">
      {quality && (
        <div className="flex items-center gap-4 flex-wrap">
          <QualityBadge score={quality} />
          {quality.kg_score && (
            <span className="text-xs text-muted-foreground">
              KG: {quality.kg_score.score?.toFixed(0)}%
            </span>
          )}
          {quality.feature_score && (
            <span className="text-xs text-muted-foreground">
              Features: {quality.feature_score.score?.toFixed(0)}%
            </span>
          )}
          {data.cross_validation?.confidence != null && data.cross_validation.confidence >= 0 && (
            <span className="text-xs text-muted-foreground">
              Validation: {data.cross_validation.confidence}% confidence
            </span>
          )}
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatCard label="Files Analyzed" value={data.file_count} />
        <StatCard label="Components Found" value={data.knowledge_graph?.nodes?.length || 0} />
        <StatCard label="Features Identified" value={data.features?.features?.length || 0} />
      </div>

      <div>
        <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">
          Project Structure
        </h3>
        <pre className="bg-card/50 border border-border/50 rounded-xl p-4 text-xs text-muted-foreground overflow-auto max-h-[500px] font-mono">
          {data.project_tree || "No tree available"}
        </pre>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">
          Analyzed Files
        </h3>
        <div className="bg-card/50 border border-border/50 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/50 text-muted-foreground">
                <th className="text-left p-3 font-medium">File</th>
                <th className="text-right p-3 font-medium">Lines</th>
                <th className="text-right p-3 font-medium">Size</th>
              </tr>
            </thead>
            <tbody>
              {(data.files || []).slice(0, 50).map((f, i) => (
                <tr key={i} className="border-b border-border/30 last:border-0">
                  <td className="p-3 font-mono text-xs">{f.path}</td>
                  <td className="p-3 text-right text-muted-foreground">{f.lines}</td>
                  <td className="p-3 text-right text-muted-foreground">{(f.size / 1024).toFixed(1)} KB</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(data.files || []).length > 50 && (
            <div className="p-3 text-sm text-muted-foreground text-center">
              ... and {data.files.length - 50} more files
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Knowledge Graph Tab ─────────────────────────────────────────────────────

const NODE_COLORS: Record<string, string> = {
  file: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  module: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  class: "bg-green-500/20 text-green-400 border-green-500/30",
  function: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  api: "bg-red-500/20 text-red-400 border-red-500/30",
  route: "bg-red-500/20 text-red-400 border-red-500/30",
  database: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  model: "bg-orange-500/20 text-orange-400 border-orange-500/30",
  service: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
  config: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  component: "bg-pink-500/20 text-pink-400 border-pink-500/30",
  page: "bg-indigo-500/20 text-indigo-400 border-indigo-500/30",
  hook: "bg-teal-500/20 text-teal-400 border-teal-500/30",
  middleware: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  util: "bg-slate-500/20 text-slate-400 border-slate-500/30",
};

function KnowledgeGraphTab({ data }: { data: AdvancedDocResult }) {
  const kg = data.knowledge_graph;
  const nodes = kg?.nodes || [];
  const edges = kg?.edges || [];
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  const selected = selectedNode ? nodeMap.get(selectedNode) : null;
  const connectedEdges = selectedNode
    ? edges.filter((e) => e.source === selectedNode || e.target === selectedNode)
    : [];

  // Group nodes by type
  const grouped = nodes.reduce<Record<string, typeof nodes>>((acc, n) => {
    (acc[n.type] = acc[n.type] || []).push(n);
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Nodes" value={nodes.length} />
        <StatCard label="Edges" value={edges.length} />
        <StatCard label="Node Types" value={Object.keys(grouped).length} />
        <StatCard label="Relationship Types" value={new Set(edges.map((e) => e.relationship)).size} />
      </div>

      {/* Mermaid overview diagram */}
      {nodes.length > 0 && nodes.length <= 80 && (
        <div>
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">
            Component Relationship Diagram
          </h3>
          <MermaidDiagram
            id="kg-overview"
            chart={buildKgMermaid(nodes, edges)}
          />
        </div>
      )}
      {nodes.length > 80 && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-4 text-sm text-amber-300">
          Mermaid diagram skipped — {nodes.length} nodes exceed the 80-node rendering limit.
          Browse individual components and connections below.
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Node list */}
        <div>
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">
            Components ({nodes.length})
          </h3>
          <div className="space-y-2 max-h-[600px] overflow-auto pr-2">
            {Object.entries(grouped).map(([type, typeNodes]) => (
              <div key={type}>
                <div className="text-xs font-semibold text-muted-foreground uppercase mb-1 mt-3">
                  {type} ({typeNodes.length})
                </div>
                {typeNodes.map((n) => (
                  <button
                    key={n.id}
                    onClick={() => setSelectedNode(n.id === selectedNode ? null : n.id)}
                    className={`w-full text-left p-2.5 rounded-lg border transition-all text-sm mb-1 ${
                      selectedNode === n.id
                        ? "bg-primary/10 border-primary/30 text-primary"
                        : `${NODE_COLORS[n.type] || NODE_COLORS.util} hover:opacity-80`
                    }`}
                  >
                    <div className="font-medium">{n.name}</div>
                    <div className="text-xs opacity-70 mt-0.5">{n.description}</div>
                  </button>
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* Selected node details */}
        <div>
          {selected ? (
            <div className="bg-card/50 border border-border/50 rounded-xl p-5 sticky top-4">
              <h3 className="font-semibold text-lg">{selected.name}</h3>
              <span className={`inline-block mt-1 px-2 py-0.5 rounded text-xs font-medium ${NODE_COLORS[selected.type] || NODE_COLORS.util}`}>
                {selected.type}
              </span>
              <p className="text-sm text-muted-foreground mt-3">{selected.description}</p>
              {selected.file_path && (
                <p className="text-xs font-mono text-muted-foreground mt-2">{selected.file_path}</p>
              )}

              {connectedEdges.length > 0 && (
                <div className="mt-4">
                  <h4 className="text-sm font-semibold mb-2">Connections ({connectedEdges.length})</h4>
                  <div className="space-y-1.5">
                    {connectedEdges.map((e, i) => {
                      const otherNode = nodeMap.get(e.source === selectedNode ? e.target : e.source);
                      const direction = e.source === selectedNode ? "→" : "←";
                      return (
                        <div key={i} className="flex items-center gap-2 text-xs">
                          <span className="text-muted-foreground">{direction}</span>
                          <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                            {e.relationship}
                          </span>
                          <button
                            onClick={() => setSelectedNode(otherNode?.id || null)}
                            className="text-primary hover:underline"
                          >
                            {otherNode?.name || "?"}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="flex items-center justify-center h-64 text-muted-foreground text-sm">
              Click a component to see its details and connections
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function buildKgMermaid(
  nodes: { id: string; name: string; type: string }[],
  edges: { source: string; target: string; relationship: string }[],
): string {
  const sanitize = (s: string) => s.replace(/["\[\](){}#&;]/g, "").replace(/\n/g, " ").slice(0, 40);
  const lines = ["graph LR"];
  const nodeIds = new Set(nodes.map((n) => n.id));
  for (const n of nodes) {
    const shape = n.type === "api" || n.type === "route" ? `{{${sanitize(n.name)}}}` :
                  n.type === "database" || n.type === "model" ? `[(${sanitize(n.name)})]` :
                  n.type === "component" || n.type === "page" ? `[/${sanitize(n.name)}/]` :
                  `[${sanitize(n.name)}]`;
    lines.push(`    ${n.id}${shape}`);
  }
  for (const e of edges) {
    if (nodeIds.has(e.source) && nodeIds.has(e.target)) {
      lines.push(`    ${e.source} -->|${sanitize(e.relationship)}| ${e.target}`);
    }
  }
  return lines.join("\n");
}

// ─── Features Tab ────────────────────────────────────────────────────────────

function FeaturesTab({ data }: { data: AdvancedDocResult }) {
  const features = data.features?.features || [];
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground mb-4">{features.length} features identified</p>
      {features.map((f) => (
        <div key={f.id} className="bg-card/50 border border-border/50 rounded-xl overflow-hidden">
          <button
            onClick={() => setExpanded(expanded === f.id ? null : f.id)}
            className="w-full text-left p-4 flex items-center gap-3 hover:bg-muted/30 transition-colors"
          >
            <ChevronRight className={`w-4 h-4 transition-transform ${expanded === f.id ? "rotate-90" : ""}`} />
            <div className="flex-1 min-w-0">
              <div className="font-medium">{f.name}</div>
              <div className="text-sm text-muted-foreground truncate">{f.description}</div>
            </div>
            <span className="text-xs text-muted-foreground shrink-0">{f.components.length} components</span>
          </button>

          {expanded === f.id && (
            <div className="border-t border-border/50 p-4 space-y-4">
              {f.data_flow && (
                <div>
                  <h4 className="text-xs font-semibold text-muted-foreground uppercase mb-1">Data Flow</h4>
                  <p className="text-sm">{f.data_flow}</p>
                </div>
              )}

              {f.entry_points?.length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold text-muted-foreground uppercase mb-1">Entry Points</h4>
                  <div className="flex flex-wrap gap-1.5">
                    {f.entry_points.map((ep, i) => (
                      <span key={i} className="px-2 py-0.5 rounded bg-primary/10 text-primary text-xs">{ep}</span>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <h4 className="text-xs font-semibold text-muted-foreground uppercase mb-2">Linked Components</h4>
                <div className="space-y-1.5">
                  {f.components.map((c, i) => (
                    <div key={i} className="flex items-start gap-3 text-sm p-2 rounded-lg bg-muted/30">
                      <span className={`shrink-0 px-1.5 py-0.5 rounded text-xs font-medium ${NODE_COLORS[c.type] || NODE_COLORS.util}`}>
                        {c.type}
                      </span>
                      <div className="min-w-0">
                        <div className="font-medium">{c.name}</div>
                        <div className="text-xs text-muted-foreground">{c.role}</div>
                        {c.file_path && (
                          <div className="text-xs font-mono text-muted-foreground mt-0.5">{c.file_path}</div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ─── Feature Connections Tab ─────────────────────────────────────────────────

function ConnectionsTab({ data }: { data: AdvancedDocResult }) {
  const connections = data.feature_connections?.connections || [];
  const groups = data.feature_connections?.feature_groups || [];
  const features = data.features?.features || [];
  const featureMap = new Map(features.map((f) => [f.id, f.name]));

  return (
    <div className="space-y-6">
      {/* Connection diagram */}
      {connections.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">
            Feature Connection Map
          </h3>
          <MermaidDiagram
            id="feature-connections"
            chart={buildConnectionMermaid(connections, featureMap)}
          />
        </div>
      )}

      {/* Groups */}
      {groups.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">
            Feature Groups
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {groups.map((g, i) => (
              <div key={i} className="bg-card/50 border border-border/50 rounded-xl p-4">
                <h4 className="font-medium">{g.group_name}</h4>
                <p className="text-sm text-muted-foreground mt-1">{g.description}</p>
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {g.feature_ids.map((fid) => (
                    <span key={fid} className="px-2 py-0.5 rounded bg-primary/10 text-primary text-xs">
                      {featureMap.get(fid) || fid}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Connection list */}
      <div>
        <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">
          All Connections ({connections.length})
        </h3>
        <div className="space-y-2">
          {connections.map((c, i) => (
            <div key={i} className="bg-card/50 border border-border/50 rounded-xl p-4">
              <div className="flex items-center gap-2 text-sm">
                <span className="font-medium text-primary">{featureMap.get(c.source_feature) || c.source_feature}</span>
                <span className="px-1.5 py-0.5 rounded bg-muted text-xs text-muted-foreground">{c.connection_type}</span>
                <span className="font-medium text-primary">{featureMap.get(c.target_feature) || c.target_feature}</span>
              </div>
              <p className="text-sm text-muted-foreground mt-1">{c.description}</p>
              {c.shared_components?.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-2">
                  {c.shared_components.map((sc, j) => (
                    <span key={j} className="px-1.5 py-0.5 rounded bg-muted text-xs text-muted-foreground">{sc}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function buildConnectionMermaid(
  connections: { source_feature: string; target_feature: string; connection_type: string }[],
  featureMap: Map<string, string>,
) {
  const sanitize = (s: string) => s.replace(/["\[\](){}#&;]/g, "").replace(/\n/g, " ").slice(0, 40);
  const lines = ["graph LR"];
  const ids = new Set<string>();
  for (const c of connections) {
    if (!ids.has(c.source_feature)) {
      ids.add(c.source_feature);
      lines.push(`    ${c.source_feature}[${sanitize(featureMap.get(c.source_feature) || c.source_feature)}]`);
    }
    if (!ids.has(c.target_feature)) {
      ids.add(c.target_feature);
      lines.push(`    ${c.target_feature}[${sanitize(featureMap.get(c.target_feature) || c.target_feature)}]`);
    }
    lines.push(`    ${c.source_feature} -->|${sanitize(c.connection_type)}| ${c.target_feature}`);
  }
  return lines.join("\n");
}

// ─── Flow Diagrams Tab ──────────────────────────────────────────────────────

function FlowsTab({ data }: { data: AdvancedDocResult }) {
  const diagrams = data.flow_diagrams?.diagrams || [];
  const overview = data.flow_diagrams?.system_overview_diagram;

  return (
    <div className="space-y-8">
      {overview && (
        <div>
          <h3 className="text-lg font-semibold mb-2">{overview.title}</h3>
          <p className="text-sm text-muted-foreground mb-3">{overview.description}</p>
          <MermaidDiagram id="system-overview" chart={overview.mermaid} />
        </div>
      )}

      {diagrams.map((d, i) => (
        <div key={i}>
          <h3 className="text-lg font-semibold mb-2">{d.title}</h3>
          <p className="text-sm text-muted-foreground mb-3">{d.description}</p>
          <MermaidDiagram id={`flow-${i}`} chart={d.mermaid} />
        </div>
      ))}

      {diagrams.length === 0 && !overview && (
        <div className="text-center text-muted-foreground py-12">No flow diagrams generated</div>
      )}
    </div>
  );
}

// ─── Technical Specs Tab ────────────────────────────────────────────────────

function TechnicalSpecsRegenElapsed({ startedAt }: { startedAt?: string }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!startedAt) return;
    const id = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [startedAt]);
  if (!startedAt) return null;
  const t = Date.parse(startedAt);
  if (Number.isNaN(t)) return null;
  const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return (
    <span className="block mt-1 text-xs text-muted-foreground tabular-nums">
      Elapsed {m > 0 ? `${m}m ` : ""}
      {s}s. Indexing huge trees can take many minutes before Claude CLI starts; the CLI step can run up to ~30 minutes
      depending on project scale.
    </span>
  );
}

function SpecSection({ title, icon: Icon, children }: { title: string; icon: typeof Shield; children: React.ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="border border-border/50 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-5 py-3.5 bg-card/80 hover:bg-card transition-colors text-left"
      >
        <Icon className="w-5 h-5 text-primary shrink-0" />
        <span className="font-semibold text-sm flex-1">{title}</span>
        <ChevronRight className={`w-4 h-4 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`} />
      </button>
      {open && <div className="p-5 space-y-4 bg-background/50">{children}</div>}
    </div>
  );
}

/** LLM output may use objects (e.g. `{ name, file_path }`) where the UI expects strings. */
function formatSpecScalar(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(formatSpecScalar).filter(Boolean).join(", ");
  if (typeof value === "object") {
    const o = value as Record<string, unknown>;
    if (typeof o.name === "string" && typeof o.file_path === "string") {
      return `${o.name} (${o.file_path})`;
    }
    if (typeof o.name === "string") return o.name;
    if (typeof o.file_path === "string") return o.file_path;
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function TechnicalSpecsTab({ data, docId }: { data: AdvancedDocResult; docId: string }) {
  const specs = data.technical_specs;
  const regenSpecs = useRegenerateAdvancedTechnicalSpecs(docId);
  const regenRunning = data.section_jobs?.technical_specs === "running";
  const busy = regenRunning || regenSpecs.isPending;
  const hasFolder = Boolean(data.output_folder);
  const specErr = data.step_errors?.technical_specs;
  const regenStartedAt = data.section_jobs?.technical_specs_started_at;

  const regenBtn = (
    <button
      type="button"
      disabled={busy || !hasFolder}
      title={
        !hasFolder
          ? "Only available for projects analyzed from a ZIP upload (extracted files must still be on the server)."
          : undefined
      }
      onClick={() =>
        regenSpecs.mutate(undefined, {
          onError: (e: Error) => alert(e.message),
        })
      }
      className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-primary/10 text-primary border border-primary/20 hover:bg-primary/15 disabled:opacity-50 disabled:pointer-events-none transition-colors shrink-0"
    >
      {busy ? (
        <Loader2 className="w-4 h-4 animate-spin shrink-0" />
      ) : (
        <RotateCcw className="w-4 h-4 shrink-0" />
      )}
      {busy ? "Regenerating…" : "Regenerate technical specs"}
    </button>
  );

  if (!specs || Object.keys(specs).length === 0) {
    return (
      <div className="space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
          <div className="text-sm text-muted-foreground max-w-xl">
            <p>
              Technical specs come from a dedicated Claude CLI pass that must return a large JSON object. They are often
              missing when that step times out, the model wraps JSON in extra text, or this report was imported without an
              on-disk codebase.
            </p>
            {busy && (
              <p className="mt-2 text-primary text-xs">
                Regeneration in progress on the server — keep this tab open. Check the Python API console for log lines
                starting with <code className="text-muted-foreground">regenerate_technical_specs</code>.
              </p>
            )}
            {busy && <TechnicalSpecsRegenElapsed startedAt={regenStartedAt} />}
          </div>
          {regenBtn}
        </div>
        {specErr && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-left">
            <p className="text-xs font-semibold text-destructive mb-1">Last error</p>
            <pre className="text-xs text-muted-foreground whitespace-pre-wrap break-words max-h-48 overflow-y-auto font-mono">
              {specErr}
            </pre>
          </div>
        )}
        <div className="text-center py-12 text-muted-foreground rounded-xl border border-border/50 border-dashed bg-card/30">
          <ClipboardList className="w-12 h-12 mx-auto mb-3 opacity-40" />
          <p className="text-lg font-medium text-foreground">No technical specifications in this report</p>
          <p className="text-sm mt-2 max-w-md mx-auto">
            Use the button above to run only this step again (requires uploaded ZIP data still present on the server).
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pb-2 border-b border-border/40">
        <div className="text-xs text-muted-foreground max-w-xl">
          <p>Re-run technical-spec extraction if results were incomplete or the step failed earlier.</p>
          {busy && (
            <p className="mt-1 text-primary">
              Regenerating on the server — this page polls every few seconds. If it never finishes, restart the API (stale
              jobs are cleared on load) and check logs for <code className="text-muted-foreground">claude</code> errors.
            </p>
          )}
          {busy && <TechnicalSpecsRegenElapsed startedAt={regenStartedAt} />}
        </div>
        {regenBtn}
      </div>
      {specs.scope_definition && (
        <SpecSection title="Scope Definition" icon={Layers}>
          {specs.scope_definition.summary && (
            <p className="text-sm text-muted-foreground">{formatSpecScalar(specs.scope_definition.summary)}</p>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-green-400 mb-2">In Scope</h4>
              <ul className="space-y-1">
                {(specs.scope_definition.in_scope || []).map((item, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <CheckCircle2 className="w-3.5 h-3.5 text-green-400 mt-0.5 shrink-0" />
                    <span>{formatSpecScalar(item)}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-red-400 mb-2">Out of Scope</h4>
              <ul className="space-y-1">
                {(specs.scope_definition.out_of_scope || []).map((item, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <X className="w-3.5 h-3.5 text-red-400 mt-0.5 shrink-0" />
                    <span>{formatSpecScalar(item)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </SpecSection>
      )}

      {specs.solution_overview && (
        <SpecSection title="Solution Overview" icon={Sparkles}>
          <p className="text-sm">{formatSpecScalar(specs.solution_overview.summary)}</p>
          {specs.solution_overview.deployment_model && (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Deployment:</span>
              <span className="px-2 py-0.5 rounded bg-primary/10 text-primary text-xs font-medium">{formatSpecScalar(specs.solution_overview.deployment_model)}</span>
            </div>
          )}
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Tech Stack</h4>
            <div className="flex flex-wrap gap-1.5">
              {(specs.solution_overview.tech_stack || []).map((t, i) => (
                <span key={i} className="px-2.5 py-1 rounded-lg text-xs font-medium bg-violet-500/10 text-violet-400 border border-violet-500/20">{formatSpecScalar(t)}</span>
              ))}
            </div>
          </div>
          {(specs.solution_overview.key_capabilities || []).length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Key Capabilities</h4>
              <ul className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
                {specs.solution_overview.key_capabilities.map((c, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <CheckCircle2 className="w-3.5 h-3.5 text-primary mt-0.5 shrink-0" />
                    <span>{formatSpecScalar(c)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </SpecSection>
      )}

      {specs.high_level_architecture && (
        <SpecSection title="High-Level Architecture" icon={Layers}>
          <p className="text-sm">{formatSpecScalar(specs.high_level_architecture.description)}</p>
          {(specs.high_level_architecture.layers || []).length > 0 && (
            <div className="space-y-3">
              {specs.high_level_architecture.layers.map((layer, i) => (
                <div key={i} className="bg-card/60 rounded-lg p-4 border border-border/30">
                  <h4 className="font-semibold text-sm mb-1">{formatSpecScalar(layer.name)}</h4>
                  <p className="text-xs text-muted-foreground mb-2">{formatSpecScalar(layer.description)}</p>
                  <div className="flex flex-wrap gap-1">
                    {(layer.components || []).map((c, j) => (
                      <span key={j} className="px-2 py-0.5 rounded text-xs bg-blue-500/10 text-blue-400 border border-blue-500/20">{formatSpecScalar(c)}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
          {specs.high_level_architecture.mermaid_diagram && (
            <MermaidDiagram
              id="arch-overview"
              chart={specs.high_level_architecture.mermaid_diagram}
              variant="wide"
            />
          )}
        </SpecSection>
      )}

      {specs.erd && (
        <SpecSection title="Entity Relationship Diagram (ERD)" icon={Database}>
          <p className="text-sm">{formatSpecScalar(specs.erd.description)}</p>
          {(specs.erd.entities || []).length > 0 && (
            <div className="space-y-3">
              {specs.erd.entities.map((entity, i) => {
                const typeLabel =
                  typeof entity.type === "string" ? entity.type : formatSpecScalar(entity.type);
                const isCustom = String(typeLabel).toLowerCase() === "custom";
                return (
                <div key={i} className="bg-card/60 rounded-lg p-4 border border-border/30">
                  <div className="flex items-center gap-2 mb-2">
                    <h4 className="font-semibold text-sm">{formatSpecScalar(entity.name)}</h4>
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${isCustom ? "bg-amber-500/10 text-amber-400" : "bg-blue-500/10 text-blue-400"}`}>
                      {typeLabel}
                    </span>
                  </div>
                  {(entity.fields || []).length > 0 && (
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b border-border/30">
                            <th className="text-left py-1.5 px-2 text-muted-foreground font-medium">Field</th>
                            <th className="text-left py-1.5 px-2 text-muted-foreground font-medium">Type</th>
                            <th className="text-left py-1.5 px-2 text-muted-foreground font-medium">Description</th>
                            <th className="text-center py-1.5 px-2 text-muted-foreground font-medium">Key</th>
                            <th className="text-center py-1.5 px-2 text-muted-foreground font-medium">Req</th>
                          </tr>
                        </thead>
                        <tbody>
                          {entity.fields.map((f, j) => (
                            <tr key={j} className="border-b border-border/10">
                              <td className="py-1.5 px-2 font-mono">{formatSpecScalar(f.name)}</td>
                              <td className="py-1.5 px-2 text-muted-foreground">{formatSpecScalar(f.type)}</td>
                              <td className="py-1.5 px-2 text-muted-foreground">{formatSpecScalar(f.description)}</td>
                              <td className="py-1.5 px-2 text-center">{f.is_key ? <KeyRound className="w-3 h-3 text-amber-400 inline" /> : ""}</td>
                              <td className="py-1.5 px-2 text-center">{f.is_required ? <CheckCircle2 className="w-3 h-3 text-green-400 inline" /> : ""}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {(entity.relationships || []).length > 0 && (
                    <div className="mt-2">
                      <p className="text-xs text-muted-foreground font-medium mb-1">Relationships:</p>
                      <ul className="space-y-0.5">
                        {entity.relationships.map((r, j) => (
                          <li key={j} className="text-xs text-muted-foreground flex items-start gap-1.5">
                            <Link2 className="w-3 h-3 mt-0.5 shrink-0" />
                            {formatSpecScalar(r)}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
                );
              })}
            </div>
          )}
          {specs.erd.mermaid_diagram && (
            <MermaidDiagram id="erd-diagram" chart={specs.erd.mermaid_diagram} variant="wide" />
          )}
        </SpecSection>
      )}

      {specs.standard_and_custom_entities && (
        <SpecSection title="Standard & Custom Entities" icon={Database}>
          {(specs.standard_and_custom_entities.standard_entities || []).length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-blue-400 mb-2">Standard Entities</h4>
              <div className="space-y-2">
                {specs.standard_and_custom_entities.standard_entities.map((e, i) => (
                  <div key={i} className="bg-card/60 rounded-lg p-3 border border-border/30">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-semibold text-sm">{formatSpecScalar(e.name)}</span>
                      <span className="px-1.5 py-0.5 rounded text-xs bg-blue-500/10 text-blue-400">standard</span>
                    </div>
                    <p className="text-xs text-muted-foreground">{formatSpecScalar(e.purpose)}</p>
                    {(e.customizations || []).length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {e.customizations.map((c, j) => (
                          <span key={j} className="px-2 py-0.5 rounded text-xs bg-amber-500/10 text-amber-400">{formatSpecScalar(c)}</span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          {(specs.standard_and_custom_entities.custom_entities || []).length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-amber-400 mb-2">Custom Entities</h4>
              <div className="space-y-2">
                {specs.standard_and_custom_entities.custom_entities.map((e, i) => (
                  <div key={i} className="bg-card/60 rounded-lg p-3 border border-border/30">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-semibold text-sm">{formatSpecScalar(e.name)}</span>
                      <span className="px-1.5 py-0.5 rounded text-xs bg-amber-500/10 text-amber-400">custom</span>
                    </div>
                    <p className="text-xs text-muted-foreground">{formatSpecScalar(e.purpose)}</p>
                    {e.fields_summary && <p className="text-xs text-muted-foreground mt-1 italic">{formatSpecScalar(e.fields_summary)}</p>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </SpecSection>
      )}

      {specs.business_rules && (
        <SpecSection title="Business Rules & Workflow Processes" icon={Workflow}>
          {(specs.business_rules.workflows || []).length > 0 && (
            <div className="space-y-3">
              {specs.business_rules.workflows.map((wf, i) => (
                <div key={i} className="bg-card/60 rounded-lg p-4 border border-border/30">
                  <h4 className="font-semibold text-sm mb-1">{formatSpecScalar(wf.name)}</h4>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground mb-2">
                    <span className="px-2 py-0.5 rounded bg-primary/10 text-primary">Trigger: {formatSpecScalar(wf.trigger)}</span>
                  </div>
                  <p className="text-xs text-muted-foreground mb-2">{formatSpecScalar(wf.description)}</p>
                  {(wf.steps || []).length > 0 && (
                    <ol className="space-y-1">
                      {wf.steps.map((s, j) => (
                        <li key={j} className="flex items-start gap-2 text-xs">
                          <span className="w-5 h-5 rounded-full bg-primary/10 text-primary flex items-center justify-center shrink-0 text-xs font-medium">{j + 1}</span>
                          <span className="mt-0.5">{formatSpecScalar(s)}</span>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              ))}
            </div>
          )}
          {(specs.business_rules.validation_rules || []).length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Validation Rules</h4>
              <ul className="space-y-1">
                {specs.business_rules.validation_rules.map((r, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <ShieldCheck className="w-3.5 h-3.5 text-primary mt-0.5 shrink-0" />
                    <span>{formatSpecScalar(r)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(specs.business_rules.automation || []).length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Automation</h4>
              <ul className="space-y-1">
                {specs.business_rules.automation.map((a, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <Workflow className="w-3.5 h-3.5 text-amber-400 mt-0.5 shrink-0" />
                    <span>{formatSpecScalar(a)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </SpecSection>
      )}

      {specs.javascript_customizations && (
        <SpecSection title="JavaScript Logic & Client-Side Customizations" icon={Code2}>
          {(specs.javascript_customizations.client_scripts || []).length > 0 && (
            <div className="space-y-2">
              {specs.javascript_customizations.client_scripts.map((s, i) => (
                <div key={i} className="bg-card/60 rounded-lg p-3 border border-border/30">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-semibold text-sm">{formatSpecScalar(s.name)}</span>
                  </div>
                  <p className="text-xs text-muted-foreground mb-1">{formatSpecScalar(s.purpose)}</p>
                  <p className="text-xs font-mono text-muted-foreground">{formatSpecScalar(s.file_path)}</p>
                  {(s.events_handled || []).length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {s.events_handled.map((e, j) => (
                        <span key={j} className="px-2 py-0.5 rounded text-xs bg-green-500/10 text-green-400">{formatSpecScalar(e)}</span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          {(specs.javascript_customizations.web_resources || []).length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Web Resources</h4>
              <div className="flex flex-wrap gap-1.5">
                {specs.javascript_customizations.web_resources.map((r, i) => (
                  <span key={i} className="px-2.5 py-1 rounded-lg text-xs bg-blue-500/10 text-blue-400 border border-blue-500/20">{formatSpecScalar(r)}</span>
                ))}
              </div>
            </div>
          )}
          {(specs.javascript_customizations.libraries_used || []).length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Libraries Used</h4>
              <div className="flex flex-wrap gap-1.5">
                {specs.javascript_customizations.libraries_used.map((l, i) => (
                  <span key={i} className="px-2.5 py-1 rounded-lg text-xs bg-violet-500/10 text-violet-400 border border-violet-500/20">{formatSpecScalar(l)}</span>
                ))}
              </div>
            </div>
          )}
        </SpecSection>
      )}

      {specs.auth_model && (
        <SpecSection title="Authentication & Authorization Model" icon={Shield}>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="bg-card/60 rounded-lg p-3 border border-border/30">
              <p className="text-xs text-muted-foreground mb-1">Authentication Method</p>
              <p className="text-sm font-medium">{formatSpecScalar(specs.auth_model.authentication_method)}</p>
            </div>
            <div className="bg-card/60 rounded-lg p-3 border border-border/30">
              <p className="text-xs text-muted-foreground mb-1">Authorization Model</p>
              <p className="text-sm font-medium">{formatSpecScalar(specs.auth_model.authorization_model)}</p>
            </div>
          </div>
          {(specs.auth_model.roles || []).length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Roles</h4>
              <div className="space-y-2">
                {specs.auth_model.roles.map((role, i) => (
                  <div key={i} className="bg-card/60 rounded-lg p-3 border border-border/30">
                    <span className="font-semibold text-sm">{formatSpecScalar(role.name)}</span>
                    {(role.permissions || []).length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {role.permissions.map((p, j) => (
                          <span key={j} className="px-2 py-0.5 rounded text-xs bg-green-500/10 text-green-400">{formatSpecScalar(p)}</span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          {(specs.auth_model.security_features || []).length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Security Features</h4>
              <ul className="space-y-1">
                {specs.auth_model.security_features.map((f, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <ShieldCheck className="w-3.5 h-3.5 text-green-400 mt-0.5 shrink-0" />
                    <span>{formatSpecScalar(f)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(specs.auth_model.file_paths || []).length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">Related Files</h4>
              <div className="flex flex-wrap gap-1.5">
                {specs.auth_model.file_paths.map((p, i) => (
                  <span key={i} className="px-2 py-0.5 rounded text-xs font-mono bg-muted/50 text-muted-foreground">{formatSpecScalar(p)}</span>
                ))}
              </div>
            </div>
          )}
        </SpecSection>
      )}

      {specs.module_components && (
        <SpecSection title="Module Components (Sales, Service, Marketing)" icon={Layers}>
          {(["sales", "service", "marketing"] as const).map((mod) => {
            const modData = specs.module_components?.[mod];
            if (!modData || (modData.components || []).length === 0) return null;
            return (
              <div key={mod} className="space-y-2">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-primary mb-2 capitalize">{mod} Module</h4>
                <div className="space-y-2">
                  {modData.components.map((c, i) => (
                    <div key={i} className="bg-card/60 rounded-lg p-3 border border-border/30 flex items-start gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-semibold text-sm">{formatSpecScalar(c.name)}</span>
                          <span className="px-1.5 py-0.5 rounded text-xs bg-primary/10 text-primary">{formatSpecScalar(c.type)}</span>
                        </div>
                        <p className="text-xs text-muted-foreground">{formatSpecScalar(c.description)}</p>
                        <p className="text-xs font-mono text-muted-foreground mt-0.5">{formatSpecScalar(c.file_path)}</p>
                      </div>
                    </div>
                  ))}
                </div>
                {modData.mermaid_diagram && (
                  <MermaidDiagram id={`module-${mod}`} chart={modData.mermaid_diagram} variant="wide" />
                )}
              </div>
            );
          })}
        </SpecSection>
      )}

      {specs.integration_architecture && (
        <SpecSection title="Technical Integration Architecture" icon={Plug}>
          <p className="text-sm">{formatSpecScalar(specs.integration_architecture.description)}</p>
          {(specs.integration_architecture.integrations || []).length > 0 && (
            <div className="space-y-3">
              {specs.integration_architecture.integrations.map((intg, i) => {
                const dirRaw = formatSpecScalar(intg.direction).toLowerCase();
                return (
                <div key={i} className="bg-card/60 rounded-lg p-4 border border-border/30">
                  <div className="flex items-center gap-2 mb-2">
                    <h4 className="font-semibold text-sm">{formatSpecScalar(intg.name)}</h4>
                    <span className="px-1.5 py-0.5 rounded text-xs bg-violet-500/10 text-violet-400">{formatSpecScalar(intg.type)}</span>
                    <span className={`px-1.5 py-0.5 rounded text-xs ${
                      dirRaw === "bidirectional" ? "bg-amber-500/10 text-amber-400" :
                      dirRaw === "inbound" ? "bg-green-500/10 text-green-400" :
                      "bg-blue-500/10 text-blue-400"
                    }`}>{formatSpecScalar(intg.direction)}</span>
                  </div>
                  <p className="text-xs text-muted-foreground mb-1">
                    <span className="font-medium">External System:</span> {formatSpecScalar(intg.external_system)}
                  </p>
                  <p className="text-xs text-muted-foreground mb-2">{formatSpecScalar(intg.description)}</p>
                  {(intg.endpoints || []).length > 0 && (
                    <div className="mb-1">
                      <span className="text-xs text-muted-foreground font-medium">Endpoints:</span>
                      <div className="flex flex-wrap gap-1 mt-0.5">
                        {intg.endpoints.map((ep, j) => (
                          <span key={j} className="px-2 py-0.5 rounded text-xs font-mono bg-muted/50 text-muted-foreground">{formatSpecScalar(ep)}</span>
                        ))}
                      </div>
                    </div>
                  )}
                  {(intg.file_paths || []).length > 0 && (
                    <div>
                      <span className="text-xs text-muted-foreground font-medium">Files:</span>
                      <div className="flex flex-wrap gap-1 mt-0.5">
                        {intg.file_paths.map((fp, j) => (
                          <span key={j} className="px-2 py-0.5 rounded text-xs font-mono bg-muted/50 text-muted-foreground">{formatSpecScalar(fp)}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
                );
              })}
            </div>
          )}
          {specs.integration_architecture.mermaid_diagram && (
            <MermaidDiagram
              id="integration-arch"
              chart={specs.integration_architecture.mermaid_diagram}
              variant="wide"
            />
          )}
        </SpecSection>
      )}

      {specs.integration_auth && (specs.integration_auth.mechanisms || []).length > 0 && (
        <SpecSection title="Integration Authentication Mechanisms" icon={KeyRound}>
          <div className="space-y-3">
            {specs.integration_auth.mechanisms.map((mech, i) => (
              <div key={i} className="bg-card/60 rounded-lg p-4 border border-border/30">
                <div className="flex items-center gap-2 mb-2">
                  <h4 className="font-semibold text-sm">{formatSpecScalar(mech.integration_name)}</h4>
                  <span className="px-1.5 py-0.5 rounded text-xs bg-amber-500/10 text-amber-400">{formatSpecScalar(mech.auth_type)}</span>
                </div>
                <p className="text-xs text-muted-foreground mb-1">{formatSpecScalar(mech.description)}</p>
                {mech.token_management && (
                  <p className="text-xs text-muted-foreground">
                    <span className="font-medium">Token Management:</span> {formatSpecScalar(mech.token_management)}
                  </p>
                )}
                {(mech.file_paths || []).length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {mech.file_paths.map((fp, j) => (
                      <span key={j} className="px-2 py-0.5 rounded text-xs font-mono bg-muted/50 text-muted-foreground">{formatSpecScalar(fp)}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </SpecSection>
      )}
    </div>
  );
}

// ─── Documentation Tab ──────────────────────────────────────────────────────

function DocumentationTab({ data }: { data: AdvancedDocResult }) {
  const [renderError, setRenderError] = useState<string | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  function exportPDF() {
    const html = contentRef.current?.innerHTML || "";
    const projectName = data.name || "Documentation";
    const w = window.open("", "_blank", "width=960,height=720");
    if (!w) return;
    w.document.write(`<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>${projectName}</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 860px; margin: 40px auto; padding: 0 24px; color: #111; line-height: 1.7; font-size: 14px; }
    h1 { font-size: 2em; border-bottom: 2px solid #222; padding-bottom: 8px; margin-top: 0; }
    h2 { font-size: 1.5em; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-top: 2em; }
    h3 { font-size: 1.2em; margin-top: 1.5em; }
    h4 { font-size: 1em; margin-top: 1em; }
    p { margin: 0.75em 0; color: #333; }
    a { color: #0066cc; }
    strong { font-weight: 600; color: #111; }
    code { background: #f2f2f2; padding: 2px 6px; border-radius: 3px; font-family: 'SFMono-Regular', Consolas, monospace; font-size: 0.88em; }
    pre { background: #f5f5f5; border: 1px solid #e0e0e0; border-radius: 6px; padding: 14px 16px; overflow: auto; }
    pre code { background: none; padding: 0; }
    table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.92em; }
    th { background: #f5f5f5; font-weight: 600; text-align: left; padding: 8px 12px; border: 1px solid #ddd; }
    td { padding: 7px 12px; border: 1px solid #ddd; color: #333; }
    blockquote { border-left: 4px solid #aaa; margin: 1em 0; padding: 0.5em 1em; color: #555; background: #fafafa; }
    ul, ol { padding-left: 1.6em; color: #333; }
    li { margin: 0.3em 0; }
    hr { border: none; border-top: 1px solid #ddd; margin: 2em 0; }
    @media print {
      body { margin: 0; max-width: 100%; }
      a { color: inherit; text-decoration: none; }
    }
  </style>
</head>
<body>
  <h1>${projectName}</h1>
  <p style="color:#666;font-size:0.875em;margin-top:-8px">Generated Documentation</p>
  <hr>
  ${html}
  <script>window.addEventListener('load', function(){ setTimeout(function(){ window.print(); }, 400); });<\/script>
</body>
</html>`);
    w.document.close();
  }

  function exportWord() {
    const html = contentRef.current?.innerHTML || "";
    const projectName = data.name || "Documentation";
    const wordHtml = `<!DOCTYPE html>
<html xmlns:o="urn:schemas-microsoft-com:office:office"
      xmlns:w="urn:schemas-microsoft-com:office:word"
      xmlns="http://www.w3.org/TR/REC-html40">
<head>
  <meta charset="utf-8">
  <title>${projectName}</title>
  <!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View><w:Zoom>90</w:Zoom><w:DoNotOptimizeForBrowser/></w:WordDocument></xml><![endif]-->
  <style>
    body { font-family: Calibri, 'Segoe UI', sans-serif; font-size: 11pt; line-height: 1.6; margin: 72pt 72pt 72pt 72pt; color: #111; }
    h1 { font-size: 22pt; color: #111; border-bottom: 1pt solid #333; padding-bottom: 6pt; margin-top: 0; }
    h2 { font-size: 16pt; color: #222; border-bottom: 1pt solid #ccc; padding-bottom: 4pt; margin-top: 18pt; }
    h3 { font-size: 13pt; color: #333; margin-top: 14pt; }
    h4 { font-size: 11pt; color: #444; margin-top: 10pt; font-weight: bold; }
    p { margin: 5pt 0; color: #222; }
    a { color: #0563c1; }
    strong { font-weight: bold; }
    code { font-family: Consolas, 'Courier New', monospace; font-size: 9pt; background: #f0f0f0; }
    pre { font-family: Consolas, 'Courier New', monospace; font-size: 9pt; background: #f5f5f5; border: 1pt solid #ddd; padding: 8pt; margin: 8pt 0; }
    pre code { background: none; }
    table { border-collapse: collapse; width: 100%; font-size: 10pt; margin: 8pt 0; }
    th { border: 1pt solid #bbbbbb; padding: 5pt 8pt; background: #f2f2f2; font-weight: bold; }
    td { border: 1pt solid #bbbbbb; padding: 4pt 8pt; color: #333; }
    blockquote { margin-left: 20pt; padding-left: 10pt; border-left: 3pt solid #999; color: #555; }
    ul, ol { margin: 4pt 0; padding-left: 22pt; }
    li { margin: 2pt 0; color: #333; }
    hr { border: none; border-top: 1pt solid #ccc; margin: 14pt 0; }
  </style>
</head>
<body>
  <h1>${projectName}</h1>
  <p style="color:#666;font-size:9pt;margin-top:0">Generated Documentation</p>
  <hr>
  ${html}
</body>
</html>`;
    const blob = new Blob(["\ufeff", wordHtml], { type: "application/msword" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${projectName.replace(/[^a-z0-9]/gi, "_")}_documentation.doc`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (!data.documentation) {
    return <div className="text-center text-muted-foreground py-12">No documentation generated</div>;
  }

  if (renderError) {
    return (
      <div className="space-y-4">
        <div className="p-4 rounded-lg bg-destructive/10 text-destructive text-sm">
          <p className="font-medium mb-1">Markdown render error: {renderError}</p>
        </div>
        <pre className="bg-card/50 border border-border/50 rounded-xl p-4 text-xs text-muted-foreground overflow-auto max-h-[600px] whitespace-pre-wrap font-mono">
          {data.documentation}
        </pre>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end gap-2">
        <button
          onClick={exportWord}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-blue-500/10 text-blue-400 hover:bg-blue-500/20 border border-blue-500/20 text-sm font-medium transition-colors"
        >
          <FileText className="w-4 h-4" /> Export Word
        </button>
        <button
          onClick={exportPDF}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-red-500/10 text-red-400 hover:bg-red-500/20 border border-red-500/20 text-sm font-medium transition-colors"
        >
          <Download className="w-4 h-4" /> Export PDF
        </button>
      </div>
      <DocMarkdown content={data.documentation} containerRef={contentRef} />
    </div>
  );
}

// ─── Progress Stepper (SSE-driven) ──────────────────────────────────────────

const STEP_ICONS: Record<string, typeof FileCode2> = {
  extraction: FolderTree,
  knowledge_graph: Network,
  features: ListTree,
  cross_validation: ShieldCheck,
  feature_connections: Link2,
  flow_diagrams: GitBranch,
  documentation: FileText,
  quality_check: BarChart3,
};

const STATUS_STYLES: Record<StepStatus, { ring: string; icon: string; bg: string; text: string }> = {
  pending:  { ring: "border-border/50",       icon: "text-muted-foreground/40", bg: "bg-muted/30",           text: "text-muted-foreground/60" },
  running:  { ring: "border-primary",         icon: "text-primary",             bg: "bg-primary/10",         text: "text-foreground" },
  complete: { ring: "border-green-500",       icon: "text-green-500",           bg: "bg-green-500/10",       text: "text-foreground" },
  error:    { ring: "border-destructive",     icon: "text-destructive",         bg: "bg-destructive/10",     text: "text-foreground" },
  skipped:  { ring: "border-yellow-500/50",   icon: "text-yellow-500/60",       bg: "bg-yellow-500/5",       text: "text-muted-foreground" },
};

function ProgressStepper({ steps }: { steps: StreamStep[] }) {
  return (
    <div className="space-y-1">
      {steps.map((s, i) => {
        const Icon = STEP_ICONS[s.step] || CircleDot;
        const style = STATUS_STYLES[s.status];
        const isLast = i === steps.length - 1;

        return (
          <div key={s.step} className="flex items-stretch gap-3">
            {/* Vertical timeline connector */}
            <div className="flex flex-col items-center w-9 shrink-0">
              <div className={`w-9 h-9 rounded-full border-2 flex items-center justify-center ${style.ring} ${style.bg} transition-all duration-500`}>
                {s.status === "running" ? (
                  <Loader2 className={`w-4 h-4 animate-spin ${style.icon}`} />
                ) : s.status === "complete" ? (
                  <CheckCircle2 className={`w-4 h-4 ${style.icon}`} />
                ) : s.status === "error" ? (
                  <AlertCircle className={`w-4 h-4 ${style.icon}`} />
                ) : s.status === "skipped" ? (
                  <SkipForward className={`w-3.5 h-3.5 ${style.icon}`} />
                ) : (
                  <Icon className={`w-4 h-4 ${style.icon}`} />
                )}
              </div>
              {!isLast && (
                <div className={`w-0.5 flex-1 min-h-[16px] transition-colors duration-500 ${
                  s.status === "complete" ? "bg-green-500/40" :
                  s.status === "running" ? "bg-primary/30" :
                  "bg-border/30"
                }`} />
              )}
            </div>

            {/* Step content */}
            <div className={`flex-1 pb-3 ${isLast ? "" : ""}`}>
              <div className={`flex items-center gap-2 h-9 ${style.text} transition-colors duration-300`}>
                <span className={`text-sm font-medium ${s.status === "pending" ? "opacity-50" : ""}`}>
                  {s.label}
                </span>
                {s.status === "running" && (
                  <span className="text-xs text-primary animate-pulse">processing...</span>
                )}
              </div>
              {s.summary && (s.status === "complete" || s.status === "running") && (
                <p className="text-xs text-muted-foreground -mt-1">{s.summary}</p>
              )}
              {s.error && (s.status === "error" || s.status === "skipped") && (
                <p className="text-xs text-destructive/80 -mt-1 truncate max-w-lg" title={s.error}>
                  {s.error}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── Stat Card ──────────────────────────────────────────────────────────────

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="bg-card/50 border border-border/50 rounded-xl p-4">
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-xs text-muted-foreground mt-1">{label}</div>
    </div>
  );
}

// ─── Main Page ──────────────────────────────────────────────────────────────

export default function AdvancedDocs() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [streamingId, setStreamingId] = useState<string | null>(null);

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadName, setUploadName] = useState("");

  const { data: docList } = useAdvancedDocsList();
  const { data: selectedDoc } = useAdvancedDoc(
    selectedId && selectedId !== streamingId ? selectedId : null,
  );
  const uploadMut = useAdvancedDocUpload();
  const deleteMut = useDeleteAdvancedDoc();
  const importMut = useImportAdvancedDoc();
  const stream = useAdvancedDocStream(streamingId);
  const importFileRef = useRef<HTMLInputElement>(null);
  const [exportingId, setExportingId] = useState<string | null>(null);

  const [unlocked, setUnlocked] = useState(false);
  const [passInput, setPassInput] = useState("");
  const [passError, setPassError] = useState(false);
  const UPLOAD_PASS = "Papun@1996";

  const handleUnlock = useCallback(() => {
    if (passInput === UPLOAD_PASS) {
      setUnlocked(true);
      setPassError(false);
      setPassInput("");
    } else {
      setPassError(true);
    }
  }, [passInput]);

  const handleUpload = useCallback(async () => {
    if (!unlocked || !uploadFile) return;
    try {
      const result = await uploadMut.mutateAsync({
        file: uploadFile,
        name: uploadName || uploadFile.name.replace(".zip", ""),
      });
      setSelectedId(result.id);
      setStreamingId(result.id);
      stream.connect(result.id);
      setUploadFile(null);
      setUploadName("");
    } catch (e: any) {
      alert(e?.message || "Upload failed");
    }
  }, [unlocked, uploadFile, uploadName, uploadMut, stream]);

  const handleExport = useCallback(async (id: string) => {
    setExportingId(id);
    try {
      await exportAdvancedDoc(id);
    } catch (e: any) {
      alert(e?.message || "Export failed");
    } finally {
      setExportingId(null);
    }
  }, []);

  const handleImport = useCallback(async (file: File) => {
    try {
      await importMut.mutateAsync(file);
    } catch (e: any) {
      alert(e?.message || "Import failed — make sure it's a valid report JSON.");
    }
  }, [importMut]);

  const handleDelete = useCallback(
    async (id: string) => {
      if (!confirm("Delete this project and all its analysis?")) return;
      await deleteMut.mutateAsync(id);
      if (selectedId === id) {
        setSelectedId(null);
        setStreamingId(null);
        stream.disconnect();
      }
    },
    [deleteMut, selectedId, stream],
  );

  const handleBack = useCallback(() => {
    setSelectedId(null);
    setStreamingId(null);
    setActiveTab("overview");
    stream.disconnect();
  }, [stream]);

  // When stream finishes, stop treating it as streaming so we switch to fetched data
  useEffect(() => {
    if (stream.isDone && streamingId) {
      setStreamingId(null);
    }
  }, [stream.isDone, streamingId]);

  // ─── Streaming View (real-time progress) ────────────────────────────────

  if (streamingId && stream.isStreaming) {
    const completedCount = stream.steps.filter(
      (s) => s.status === "complete",
    ).length;

    return (
      <div className="max-w-2xl mx-auto py-12 space-y-8">
        {/* Header */}
        <div className="text-center space-y-2">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-violet-500 to-fuchsia-600 flex items-center justify-center shadow-lg shadow-violet-500/20 mx-auto">
            <Sparkles className="w-7 h-7 text-white" />
          </div>
          <h2 className="text-xl font-bold mt-4">
            Analyzing{" "}
            <span className="text-primary">
              {stream.data.name || "project"}
            </span>
          </h2>
          <p className="text-sm text-muted-foreground">
            Claude Code CLI is exploring your codebase — {completedCount}/
            {stream.steps.length} steps complete. Very large ZIPs may sit on the
            first step for a long time while the server unpacks and scans files;
            the first step shows live counts when available.
          </p>
        </div>

        {/* Overall progress bar */}
        <div className="w-full bg-muted/50 rounded-full h-2 overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-violet-500 to-fuchsia-600 rounded-full transition-all duration-700 ease-out"
            style={{
              width: `${(completedCount / stream.steps.length) * 100}%`,
            }}
          />
        </div>

        {/* Stepper */}
        <div className="bg-card/50 border border-border/50 rounded-2xl p-6">
          <ProgressStepper steps={stream.steps} />
        </div>

        <div className="text-center">
          <button
            onClick={handleBack}
            className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1 mx-auto"
          >
            <ArrowLeft className="w-4 h-4" /> Back to list
          </button>
        </div>
      </div>
    );
  }

  // ─── Detail View (full result) ──────────────────────────────────────────

  const displayDoc = selectedDoc;

  if (selectedId && displayDoc) {
    const hasAnyData =
      displayDoc.file_count > 0 ||
      (displayDoc.knowledge_graph?.nodes?.length ?? 0) > 0 ||
      (displayDoc.features?.features?.length ?? 0) > 0;

    if (displayDoc.status === "processing" && !hasAnyData) {
      return (
        <div className="flex flex-col items-center justify-center h-full gap-4 py-24">
          <Loader2 className="w-10 h-10 animate-spin text-primary" />
          <h2 className="text-xl font-semibold">
            Analyzing with Claude Code CLI...
          </h2>
          <p className="text-muted-foreground text-sm max-w-md text-center">
            Extracting code, building knowledge graph, identifying features,
            generating flow diagrams and documentation. This may take a few
            minutes.
          </p>
          {displayDoc.current_step && (
            <p className="text-xs text-muted-foreground">
              Current step: {displayDoc.current_step}
            </p>
          )}
          <button
            onClick={handleBack}
            className="mt-4 text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
          >
            <ArrowLeft className="w-4 h-4" /> Back to list
          </button>
        </div>
      );
    }

    const isError = displayDoc.status === "error";
    const isPartial = displayDoc.status === "partial";
    const isStillProcessing = displayDoc.status === "processing";
    const stepErrors = displayDoc.step_errors || {};
    const completedSteps = displayDoc.completed_steps || [];

    return (
      <div className="space-y-6">
        {/* Error/Partial/Processing banner */}
        {isError && !hasAnyData && (
          <div className="flex items-start gap-3 p-4 rounded-xl bg-destructive/10 border border-destructive/30">
            <AlertCircle className="w-5 h-5 text-destructive mt-0.5 shrink-0" />
            <div>
              <p className="font-medium text-destructive">Analysis Failed</p>
              <p className="text-sm text-muted-foreground mt-1">
                {displayDoc.error ||
                  "An unknown error occurred during analysis."}
              </p>
            </div>
          </div>
        )}
        {(isError || isPartial) && hasAnyData && (
          <div
            className={`flex items-start gap-3 p-4 rounded-xl border ${
              isPartial
                ? "bg-yellow-500/10 border-yellow-500/30"
                : "bg-destructive/10 border-destructive/30"
            }`}
          >
            <AlertCircle
              className={`w-5 h-5 mt-0.5 shrink-0 ${isPartial ? "text-yellow-500" : "text-destructive"}`}
            />
            <div className="min-w-0">
              <p
                className={`font-medium ${isPartial ? "text-yellow-500" : "text-destructive"}`}
              >
                {isPartial ? "Partial Results" : "Analysis Failed"} —{" "}
                {completedSteps.length}/5 steps completed
              </p>
              {Object.keys(stepErrors).length > 0 && (
                <ul className="text-sm text-muted-foreground mt-1 space-y-0.5">
                  {Object.entries(stepErrors).map(([step, err]) => (
                    <li key={step}>
                      <span className="font-mono text-xs">{step}</span>: {err}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
        {isStillProcessing && hasAnyData && (
          <div className="flex items-center gap-3 p-4 rounded-xl bg-primary/10 border border-primary/30">
            <Loader2 className="w-5 h-5 animate-spin text-primary shrink-0" />
            <div>
              <p className="font-medium text-primary">
                Analysis in progress...
              </p>
              {displayDoc.current_step && (
                <p className="text-sm text-muted-foreground mt-0.5">
                  Current step: {displayDoc.current_step}
                  {completedSteps.length > 0 &&
                    ` (${completedSteps.length}/5 completed)`}
                </p>
              )}
            </div>
          </div>
        )}

        {/* Header */}
        <div className="flex items-center gap-4">
          <button
            onClick={handleBack}
            className="p-2 rounded-lg hover:bg-muted transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold truncate">{displayDoc.name}</h1>
            <div className="flex items-center gap-2 mt-1 text-sm text-muted-foreground">
              {displayDoc.status === "ready" ? (
                <CheckCircle2 className="w-4 h-4 text-green-500" />
              ) : displayDoc.status === "partial" ? (
                <AlertCircle className="w-4 h-4 text-yellow-500" />
              ) : displayDoc.status === "processing" ? (
                <Loader2 className="w-4 h-4 animate-spin text-primary" />
              ) : (
                <AlertCircle className="w-4 h-4 text-destructive" />
              )}
              {displayDoc.file_count} files analyzed
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 overflow-x-auto pb-1 border-b border-border/50">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`flex items-center gap-2 px-4 py-2.5 text-sm rounded-t-lg transition-colors whitespace-nowrap ${
                  activeTab === tab.key
                    ? "bg-primary/10 text-primary border-b-2 border-primary font-medium"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                }`}
              >
                <Icon className="w-4 h-4" />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Tab content */}
        <div>
          {activeTab === "overview" && <OverviewTab data={displayDoc} />}
          {activeTab === "knowledge-graph" && (
            <KnowledgeGraphTab data={displayDoc} />
          )}
          {activeTab === "features" && <FeaturesTab data={displayDoc} />}
          {activeTab === "connections" && <ConnectionsTab data={displayDoc} />}
          {activeTab === "flows" && <FlowsTab data={displayDoc} />}
          {activeTab === "technical-specs" && (
            <TechnicalSpecsTab data={displayDoc} docId={selectedId ?? displayDoc.id} />
          )}
          {activeTab === "documentation" && (
            <DocumentationTab data={displayDoc} />
          )}
        </div>
      </div>
    );
  }

  // ─── List View ──────────────────────────────────────────────────────────────

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <div className="flex items-center gap-3 mb-2">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-fuchsia-600 flex items-center justify-center shadow-lg shadow-violet-500/20">
            <Sparkles className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-bold">Advanced Documentation</h1>
            <p className="text-sm text-muted-foreground">
              Powered by Claude Code CLI — upload a ZIP and get full code
              analysis
            </p>
          </div>
        </div>
      </div>

      {/* Upload — password-protected */}
      <div className="bg-card/50 border border-border/50 rounded-2xl p-6 relative overflow-hidden">
        <h2 className="font-semibold mb-4 flex items-center gap-2">
          {unlocked ? <Unlock className="w-5 h-5 text-emerald-400" /> : <Lock className="w-5 h-5 text-amber-400" />}
          Upload Project ZIP
          {unlocked && (
            <button onClick={() => setUnlocked(false)} className="ml-auto text-xs text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1">
              <Lock className="w-3 h-3" /> Re-lock
            </button>
          )}
        </h2>

        {/* Upload form — always rendered, blurred when locked */}
        <div className={`space-y-4 transition-all duration-300 ${!unlocked ? "blur-sm pointer-events-none select-none" : ""}`}>
          <div>
            <label className="block text-sm text-muted-foreground mb-1.5">
              Project Name
            </label>
            <input
              type="text"
              disabled={!unlocked}
              tabIndex={unlocked ? 0 : -1}
              value={uploadName}
              onChange={(e) => unlocked && setUploadName(e.target.value)}
              placeholder="My Project"
              className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
            />
          </div>
          <div>
            <label className="block text-sm text-muted-foreground mb-1.5">
              ZIP File
            </label>
            <div className="relative">
              <input
                type="file"
                accept=".zip"
                disabled={!unlocked}
                tabIndex={unlocked ? 0 : -1}
                onChange={(e) => unlocked && setUploadFile(e.target.files?.[0] || null)}
                className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm file:mr-3 file:px-3 file:py-1 file:rounded file:border-0 file:bg-primary/10 file:text-primary file:text-sm file:font-medium file:cursor-pointer"
              />
            </div>
            {uploadFile && (
              <p className="text-xs text-muted-foreground mt-1">
                {uploadFile.name} (
                {(uploadFile.size / 1024 / 1024).toFixed(1)} MB)
              </p>
            )}
          </div>
          <button
            onClick={handleUpload}
            disabled={!unlocked || !uploadFile || uploadMut.isPending}
            tabIndex={unlocked ? 0 : -1}
            className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-violet-500 to-fuchsia-600 text-white font-medium text-sm hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {uploadMut.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" /> Uploading...
              </>
            ) : (
              <>
                <Sparkles className="w-4 h-4" /> Analyze with Claude
              </>
            )}
          </button>
        </div>

        {/* Lock overlay */}
        {!unlocked && (
          <div className="absolute inset-0 rounded-2xl bg-background/60 backdrop-blur-[2px] flex flex-col items-center justify-center gap-4 z-10">
            <div className="w-14 h-14 rounded-full bg-amber-500/10 border border-amber-500/30 flex items-center justify-center">
              <KeyRound className="w-6 h-6 text-amber-400" />
            </div>
            <p className="text-sm text-muted-foreground text-center max-w-xs">
              Enter the password to unlock upload
            </p>
            <div className="flex items-center gap-2 w-full max-w-xs">
              <input
                type="password"
                value={passInput}
                onChange={(e) => { setPassInput(e.target.value); setPassError(false); }}
                onKeyDown={(e) => e.key === "Enter" && handleUnlock()}
                placeholder="Enter password"
                className={`flex-1 px-3 py-2 rounded-lg bg-background/80 border text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 ${passError ? "border-red-500 ring-2 ring-red-500/30" : "border-border"}`}
              />
              <button
                onClick={handleUnlock}
                className="px-4 py-2 rounded-lg bg-gradient-to-r from-amber-500 to-orange-600 text-white font-medium text-sm hover:opacity-90 transition-opacity flex items-center gap-1.5"
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
        )}
      </div>

      {/* Import Report */}
      <div className="bg-card/50 border border-border/50 rounded-2xl p-6">
        <h2 className="font-semibold mb-4 flex items-center gap-2">
          <FileUp className="w-5 h-5" /> Import Report
        </h2>
        <p className="text-sm text-muted-foreground mb-4">
          Import a previously exported analysis report (JSON file).
        </p>
        <input
          ref={importFileRef}
          type="file"
          accept=".json"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) {
              handleImport(file);
              e.target.value = "";
            }
          }}
        />
        <button
          onClick={() => importFileRef.current?.click()}
          disabled={importMut.isPending}
          className="px-6 py-2.5 rounded-xl border border-border bg-background hover:bg-muted text-foreground font-medium text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
        >
          {importMut.isPending ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" /> Importing...
            </>
          ) : (
            <>
              <FileUp className="w-4 h-4" /> Choose Report JSON
            </>
          )}
        </button>
      </div>

      {/* Project List */}
      {(docList || []).length > 0 && (
        <div>
          <h2 className="font-semibold mb-4">Previous Projects</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {(docList || []).map((d) => (
              <div
                key={d.id}
                className="bg-card/50 border border-border/50 rounded-xl p-4 hover:border-primary/30 transition-colors cursor-pointer group"
                onClick={() => setSelectedId(d.id)}
              >
                <div className="flex items-start justify-between">
                  <div className="min-w-0 flex-1">
                    <h3 className="font-medium truncate group-hover:text-primary transition-colors">
                      {d.name}
                    </h3>
                    <div className="flex items-center gap-2 mt-1.5">
                      <span
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
                          d.status === "ready"
                            ? "bg-green-500/10 text-green-500"
                            : d.status === "partial"
                              ? "bg-yellow-500/10 text-yellow-500"
                              : d.status === "error"
                                ? "bg-destructive/10 text-destructive"
                                : "bg-blue-500/10 text-blue-500"
                        }`}
                      >
                        {d.status === "processing" && (
                          <Loader2 className="w-3 h-3 animate-spin" />
                        )}
                        {d.status === "ready" && (
                          <CheckCircle2 className="w-3 h-3" />
                        )}
                        {d.status === "partial" && (
                          <AlertCircle className="w-3 h-3" />
                        )}
                        {d.status === "error" && (
                          <AlertCircle className="w-3 h-3" />
                        )}
                        {d.status === "partial"
                          ? "partial results"
                          : d.status}
                      </span>
                      {d.file_count > 0 && (
                        <span className="text-xs text-muted-foreground">
                          {d.file_count} files
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleExport(d.id);
                      }}
                      disabled={exportingId === d.id}
                      title="Export report"
                      className="p-1.5 rounded-lg hover:bg-primary/10 hover:text-primary opacity-0 group-hover:opacity-100 transition-all disabled:opacity-50"
                    >
                      {exportingId === d.id ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Download className="w-4 h-4" />
                      )}
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(d.id);
                      }}
                      title="Delete project"
                      className="p-1.5 rounded-lg hover:bg-destructive/10 hover:text-destructive opacity-0 group-hover:opacity-100 transition-all"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
