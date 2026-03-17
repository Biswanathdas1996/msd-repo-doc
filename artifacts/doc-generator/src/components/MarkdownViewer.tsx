import { memo, useState, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

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
