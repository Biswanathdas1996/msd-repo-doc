import { memo, useState, useCallback, useEffect, useRef, useId } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import mermaid from 'mermaid';

mermaid.initialize({
  startOnLoad: false,
  theme: 'base',
  securityLevel: 'loose',
  fontFamily: "'Inter', 'Segoe UI', system-ui, sans-serif",
  flowchart: {
    useMaxWidth: true,
    htmlLabels: true,
    curve: 'basis',
    padding: 24,
    nodeSpacing: 60,
    rankSpacing: 70,
    diagramPadding: 32,
    wrappingWidth: 220,
  },
  themeVariables: {
    darkMode: true,
    background: 'transparent',
    fontFamily: "'Inter', system-ui, sans-serif",
    fontSize: '14px',

    primaryColor: '#1e3a5f',
    primaryTextColor: '#f1f5f9',
    primaryBorderColor: '#3b82f6',

    secondaryColor: '#1a3352',
    secondaryTextColor: '#e2e8f0',
    secondaryBorderColor: '#2563eb',

    tertiaryColor: '#172554',
    tertiaryTextColor: '#e2e8f0',
    tertiaryBorderColor: '#1d4ed8',

    lineColor: '#3b82f6',
    textColor: '#f1f5f9',

    mainBkg: '#1e3a5f',
    nodeBorder: '#3b82f6',
    nodeTextColor: '#f1f5f9',

    clusterBkg: '#0c1a2e',
    clusterBorder: '#1e40af',
    titleColor: '#93c5fd',

    edgeLabelBackground: '#111827',

    noteBkgColor: '#1e293b',
    noteTextColor: '#e2e8f0',
    noteBorderColor: '#3b82f6',
  },
});

/**
 * Sanitise LLM-generated Mermaid code to fix common syntax issues
 * that cause the Mermaid parser to fail.
 */
function sanitiseMermaid(raw: string): string {
  let s = raw.trim();
  s = s.replace(/^```mermaid\s*/i, '').replace(/```\s*$/, '');
  s = s.replace(/(\b\w+)\{\{([^}]*?)\}\}/g, (_m: string, id: string, label: string) => {
    const clean = label.trim().replace(/"/g, "'");
    return `${id}["${clean}"]`;
  });
  s = s.replace(/\(\s*[·•\-–]\s*\)/g, '');
  return s.trim();
}

/* ─── Minimap ───────────────────────────────────────────────────────────── */

function Minimap({
  svgHtml,
  scale,
  translate,
  viewportSize,
  contentSize,
}: {
  svgHtml: string;
  scale: number;
  translate: { x: number; y: number };
  viewportSize: { w: number; h: number };
  contentSize: { w: number; h: number };
}) {
  const MINIMAP_W = 160;
  const MINIMAP_H = 100;

  if (contentSize.w === 0 || contentSize.h === 0) return null;

  const mapScale = Math.min(MINIMAP_W / contentSize.w, MINIMAP_H / contentSize.h) * 0.85;

  // Where the viewport "window" sits on the minimap
  const vpW = (viewportSize.w / scale) * mapScale;
  const vpH = (viewportSize.h / scale) * mapScale;
  const vpX = (MINIMAP_W / 2) - (translate.x / scale) * mapScale - vpW / 2 + ((contentSize.w * mapScale) / 2 - MINIMAP_W / 2) * 0;
  const vpY = (MINIMAP_H / 2) - (translate.y / scale) * mapScale - vpH / 2;

  return (
    <div className="absolute bottom-4 right-4 rounded-xl border border-white/[0.06] bg-[#0a0e17]/90 backdrop-blur-md shadow-2xl overflow-hidden pointer-events-none"
      style={{ width: MINIMAP_W, height: MINIMAP_H }}
    >
      <div
        className="absolute inset-0 flex items-center justify-center opacity-30 [&_svg]:!max-w-[140px] [&_svg]:!max-h-[85px]"
        dangerouslySetInnerHTML={{ __html: svgHtml }}
      />
      <div
        className="absolute rounded border border-primary/60 bg-primary/10"
        style={{
          width: Math.max(12, Math.min(vpW, MINIMAP_W)),
          height: Math.max(8, Math.min(vpH, MINIMAP_H)),
          left: Math.max(0, Math.min(vpX, MINIMAP_W - 12)),
          top: Math.max(0, Math.min(vpY, MINIMAP_H - 8)),
        }}
      />
    </div>
  );
}

/* ─── Fullscreen Mermaid Viewer ─────────────────────────────────────────── */

function FullscreenDiagramViewer({
  svgHtml,
  onClose,
}: {
  svgHtml: string;
  onClose: () => void;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const svgWrapRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });
  const [viewportSize, setViewportSize] = useState({ w: 0, h: 0 });
  const [contentSize, setContentSize] = useState({ w: 0, h: 0 });
  const dragRef = useRef<{ dragging: boolean; startX: number; startY: number; origX: number; origY: number }>({
    dragging: false, startX: 0, startY: 0, origX: 0, origY: 0,
  });

  // Measure viewport + content
  useEffect(() => {
    if (viewportRef.current) {
      const { clientWidth, clientHeight } = viewportRef.current;
      setViewportSize({ w: clientWidth, h: clientHeight });
    }
    if (svgWrapRef.current) {
      const svg = svgWrapRef.current.querySelector('svg');
      if (svg) setContentSize({ w: svg.clientWidth || 800, h: svg.clientHeight || 600 });
    }
  }, [svgHtml]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  // Prevent body scroll
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, []);

  // Pinch-to-zoom via wheel
  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY < 0 ? 0.12 : -0.12;
    setScale(prev => Math.min(6, Math.max(0.08, prev * (1 + delta))));
  }, []);

  // Drag to pan
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
      {/* ── Glassmorphic toolbar ── */}
      <div className="relative flex items-center justify-between px-5 h-14 border-b border-white/[0.06] bg-gradient-to-r from-[#0d1117]/95 via-[#101824]/95 to-[#0d1117]/95 backdrop-blur-xl">
        {/* Left: title */}
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-primary/10 border border-primary/20">
            <svg className="w-4 h-4 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9m11.25-5.25v4.5m0-4.5h-4.5m4.5 0L15 9m-11.25 11.25v-4.5m0 4.5h4.5m-4.5 0L9 15m11.25 5.25v-4.5m0 4.5h-4.5m4.5 0L15 15" />
            </svg>
          </div>
          <div>
            <span className="text-sm font-semibold text-foreground tracking-tight">Solution Flow Diagram</span>
            <span className="text-[10px] text-muted-foreground ml-2 hidden sm:inline">Interactive Viewer</span>
          </div>
        </div>

        {/* Center: zoom controls (pill) */}
        <div className="absolute left-1/2 -translate-x-1/2 flex items-center gap-0.5 bg-white/[0.04] border border-white/[0.06] rounded-full px-1 py-0.5">
          <button onClick={zoomOut} className="p-1.5 rounded-full hover:bg-white/[0.08] text-muted-foreground hover:text-foreground transition-colors" title="Zoom out (−)">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}><path strokeLinecap="round" d="M18 12H6" /></svg>
          </button>

          <button onClick={resetView} className="px-2 py-1 rounded-full hover:bg-white/[0.08] transition-colors group min-w-[52px]" title="Reset to 100%">
            <span className="text-[11px] font-medium tabular-nums text-muted-foreground group-hover:text-foreground transition-colors">{scalePercent}%</span>
          </button>

          <button onClick={zoomIn} className="p-1.5 rounded-full hover:bg-white/[0.08] text-muted-foreground hover:text-foreground transition-colors" title="Zoom in (+)">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}><path strokeLinecap="round" d="M12 6v12m6-6H6" /></svg>
          </button>

          <div className="w-px h-4 bg-white/[0.08] mx-0.5" />

          <button onClick={fitToScreen} className="px-2 py-1 rounded-full hover:bg-white/[0.08] text-muted-foreground hover:text-foreground transition-colors text-[11px] font-medium" title="Fit diagram to screen">
            Fit
          </button>
        </div>

        {/* Right: close */}
        <button onClick={onClose} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg hover:bg-red-500/10 border border-transparent hover:border-red-500/20 text-muted-foreground hover:text-red-400 transition-all text-xs font-medium" title="Close (Esc)">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
          <span className="hidden sm:inline">Close</span>
        </button>
      </div>

      {/* ── Canvas ── */}
      <div
        ref={viewportRef}
        className="relative flex-1 overflow-hidden cursor-grab active:cursor-grabbing select-none"
        style={{ background: 'radial-gradient(circle at 50% 50%, #0d1420 0%, #070a12 100%)' }}
        onWheel={handleWheel}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      >
        {/* Subtle dot grid */}
        <div className="absolute inset-0 opacity-[0.035]" style={{
          backgroundImage: 'radial-gradient(circle, #94a3b8 1px, transparent 1px)',
          backgroundSize: '28px 28px',
        }} />

        <div
          ref={contentRef}
          className="w-full h-full flex items-center justify-center"
          style={{
            transform: `translate(${translate.x}px, ${translate.y}px) scale(${scale})`,
            transformOrigin: 'center center',
            transition: dragRef.current.dragging ? 'none' : 'transform 0.18s cubic-bezier(.22,1,.36,1)',
          }}
        >
          <div
            ref={svgWrapRef}
            className="mermaid-container [&_svg]:max-w-none [&_svg]:max-h-none [&_svg]:drop-shadow-lg"
            dangerouslySetInnerHTML={{ __html: svgHtml }}
          />
        </div>

        {/* Minimap */}
        <Minimap
          svgHtml={svgHtml}
          scale={scale}
          translate={translate}
          viewportSize={viewportSize}
          contentSize={contentSize}
        />
      </div>

      {/* ── Footer ── */}
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

/* ─── MermaidBlock (inline) ─────────────────────────────────────────────── */

/**
 * Renders a single Mermaid diagram from source code.
 * Uses mermaid.render() (async) to produce SVG, then injects it into the DOM.
 * Includes an "Expand" button to open a fullscreen zoomable viewer.
 */
function MermaidBlock({ code }: { code: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const uniqueId = useId().replace(/:/g, '_');
  const [error, setError] = useState<string | null>(null);
  const [svgHtml, setSvgHtml] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const id = `mermaid_${uniqueId}`;
    setLoading(true);

    (async () => {
      try {
        const sanitised = sanitiseMermaid(code);
        let { svg } = await mermaid.render(id, sanitised);
        const parser = new DOMParser();
        const doc = parser.parseFromString(svg, 'image/svg+xml');
        const svgEl = doc.querySelector('svg');
        if (svgEl) {
          svgEl.style.backgroundColor = 'transparent';
          svgEl.removeAttribute('bgcolor');
          svg = new XMLSerializer().serializeToString(svgEl);
        }
        if (!cancelled) {
          setSvgHtml(svg);
          if (containerRef.current) {
            containerRef.current.innerHTML = svg;
          }
        }
      } catch (err: any) {
        if (!cancelled) {
          setError(err?.message || 'Invalid Mermaid syntax');
          document.getElementById(`d${id}`)?.remove();
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [code, uniqueId]);

  const openFullscreen = useCallback(() => setFullscreen(true), []);
  const closeFullscreen = useCallback(() => setFullscreen(false), []);

  if (error) {
    return (
      <div className="rounded-2xl border border-red-500/20 bg-gradient-to-br from-red-950/30 to-red-950/10 p-5 my-8 overflow-x-auto">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-5 h-5 rounded-full bg-red-500/20 flex items-center justify-center">
            <svg className="w-3 h-3 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}><path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" /></svg>
          </div>
          <p className="text-sm text-red-400 font-semibold">Diagram render error</p>
        </div>
        <pre className="text-xs text-slate-400 whitespace-pre-wrap bg-black/20 rounded-xl p-3 border border-red-500/10">{code}</pre>
        <p className="text-xs text-red-400/80 mt-3">{error}</p>
      </div>
    );
  }

  return (
    <>
      <div className="my-8 relative group">
        <div className="rounded-2xl border border-blue-500/20 bg-gradient-to-br from-[#080d19] via-[#0b1120] to-[#080d19] shadow-2xl shadow-blue-950/20 overflow-hidden">
          <div className="flex items-center justify-between px-5 py-3 bg-gradient-to-r from-blue-950/40 to-transparent border-b border-blue-500/10">
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-blue-500/10 border border-blue-500/20">
                <svg className="w-3.5 h-3.5 text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 14.25v2.25m3-4.5v4.5m3-6.75v6.75m3-9v9M6 20.25h12A2.25 2.25 0 0020.25 18V6A2.25 2.25 0 0018 3.75H6A2.25 2.25 0 003.75 6v12A2.25 2.25 0 006 20.25z" />
                </svg>
              </div>
              <div>
                <span className="text-sm font-semibold text-blue-100 tracking-tight">Solution Architecture</span>
                <span className="text-[10px] text-blue-400/60 ml-2">Interactive Diagram</span>
              </div>
            </div>
            {svgHtml && (
              <button
                onClick={openFullscreen}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-500/10 border border-blue-500/20 text-blue-300 hover:text-blue-200 hover:bg-blue-500/20 hover:border-blue-400/30 transition-all text-xs font-medium"
                title="Open fullscreen viewer"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9m11.25-5.25v4.5m0-4.5h-4.5m4.5 0L15 9m-11.25 11.25v-4.5m0 4.5h4.5m-4.5 0L9 15m11.25 5.25v-4.5m0 4.5h-4.5m4.5 0L15 15" />
                </svg>
                Expand
              </button>
            )}
          </div>

          <div className="relative min-h-[300px]"
            style={{ background: 'radial-gradient(ellipse at 50% 50%, rgba(30, 58, 95, 0.15) 0%, transparent 70%)' }}
          >
            {loading && (
              <div className="absolute inset-0 flex items-center justify-center z-10">
                <div className="flex flex-col items-center gap-3">
                  <div className="w-10 h-10 rounded-full border-2 border-blue-500/20 border-t-blue-400 animate-spin" />
                  <span className="text-xs text-blue-300/60 font-medium">Rendering diagram...</span>
                </div>
              </div>
            )}
            <div
              ref={containerRef}
              className={`mermaid-container flex justify-center overflow-auto p-8 transition-opacity duration-500 ${loading ? 'opacity-0' : 'opacity-100'}`}
            />
          </div>

          {svgHtml && (
            <div className="flex items-center justify-center py-2.5 border-t border-blue-500/10 bg-blue-950/20">
              <button
                onClick={openFullscreen}
                className="flex items-center gap-2 text-[11px] text-blue-400/50 hover:text-blue-300 transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9m11.25-5.25v4.5m0-4.5h-4.5m4.5 0L15 9m-11.25 11.25v-4.5m0 4.5h4.5m-4.5 0L9 15m11.25 5.25v-4.5m0 4.5h-4.5m4.5 0L15 15" />
                </svg>
                Click to expand fullscreen with zoom and pan
              </button>
            </div>
          )}
        </div>
      </div>

      {fullscreen && svgHtml && (
        <FullscreenDiagramViewer svgHtml={svgHtml} onClose={closeFullscreen} />
      )}
    </>
  );
}

interface MarkdownViewerProps {
  content: string;
}

export const MarkdownViewer = memo(function MarkdownViewer({ content }: MarkdownViewerProps) {
  return (
    <div className="prose prose-invert prose-slate max-w-none w-full">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ node, inline, className, children, ...props }: any) {
            const codeString = String(children).replace(/\n$/, '');
            const match = /language-(\w+)/.exec(className || '');
            const lang = match ? match[1] : '';

            // Mermaid code blocks → render as interactive diagram
            if (!inline && lang === 'mermaid') {
              return <MermaidBlock code={codeString} />;
            }

            const isBlock = !inline && (className || String(children).includes('\n'));
            return isBlock ? (
              <pre className="rounded-xl border border-border bg-[#0d1117] p-4 my-6 overflow-x-auto">
                <code {...props} className="text-sm text-slate-300 font-mono">
                  {children}
                </code>
              </pre>
            ) : (
              <code {...props} className={`${className} bg-muted px-1.5 py-0.5 rounded-md text-blue-300`}>
                {children}
              </code>
            );
          },
          h1: ({node, ...props}) => <h1 className="text-3xl font-display font-bold text-foreground mt-8 mb-4 pb-2 border-b border-border/50" {...props} />,
          h2: ({node, ...props}) => <h2 className="text-2xl font-display font-semibold text-foreground mt-8 mb-4" {...props} />,
          h3: ({node, ...props}) => <h3 className="text-xl font-display font-medium text-foreground mt-6 mb-3" {...props} />,
          a: ({node, ...props}) => <a className="text-primary hover:text-primary/80 underline decoration-primary/30 underline-offset-2 transition-colors" {...props} />,
          table: ({node, ...props}) => (
            <div className="overflow-x-auto my-6 border border-border rounded-xl">
              <table className="w-full text-sm text-left m-0" {...props} />
            </div>
          ),
          th: ({node, ...props}) => <th className="bg-muted/50 px-4 py-3 font-medium text-foreground border-b border-border" {...props} />,
          td: ({node, ...props}) => <td className="px-4 py-3 border-b border-border/50 last:border-0" {...props} />,
          blockquote: ({node, ...props}) => <blockquote className="border-l-4 border-primary/50 bg-primary/5 px-4 py-2 italic text-muted-foreground rounded-r-lg my-4" {...props} />,
        }}
      >
        {content || '*No content available.*'}
      </ReactMarkdown>
    </div>
  );
});

interface CollapsibleSectionProps {
  title: string;
  slug: string;
  content: string;
  defaultOpen?: boolean;
}

export const CollapsibleSection = memo(function CollapsibleSection({ title, slug, content, defaultOpen = false }: CollapsibleSectionProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const charCount = content?.length || 0;

  const toggle = useCallback(() => setIsOpen(prev => !prev), []);

  return (
    <div id={slug} className="border border-border/50 rounded-xl overflow-hidden">
      <button
        onClick={toggle}
        className="w-full flex items-center justify-between p-4 bg-muted/20 hover:bg-muted/40 transition-colors text-left"
      >
        <div className="flex items-center gap-3">
          <svg
            className={`w-4 h-4 text-muted-foreground transition-transform duration-200 ${isOpen ? 'rotate-90' : ''}`}
            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          <span className="font-semibold text-foreground">{title}</span>
        </div>
        <span className="text-xs text-muted-foreground">
          {charCount > 1000 ? `${Math.round(charCount / 1000)}k chars` : `${charCount} chars`}
        </span>
      </button>
      {isOpen && (
        <div className="p-6 border-t border-border/30">
          <MarkdownViewer content={content} />
        </div>
      )}
    </div>
  );
});
