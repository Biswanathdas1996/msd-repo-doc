import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';

interface MarkdownViewerProps {
  content: string;
}

export function MarkdownViewer({ content }: MarkdownViewerProps) {
  return (
    <div className="prose prose-invert prose-slate max-w-none w-full">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ node, inline, className, children, ...props }: any) {
            const match = /language-(\w+)/.exec(className || '');
            return !inline && match ? (
              <SyntaxHighlighter
                {...props}
                children={String(children).replace(/\n$/, '')}
                style={vscDarkPlus as any}
                language={match[1]}
                PreTag="div"
                className="rounded-xl border border-border !bg-[#0d1117] !my-6"
              />
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
}
