"use client";
import { createContext, useContext, type ComponentProps } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// Answers are markdown, rendered without raw HTML: a tag in an answer is shown as the text it is and never
// becomes an element, and images are never fetched. Citation markers like [1] become buttons.

const CITE = "cite:";

/** Called when a marker in the text is chosen, with the marker's number and the button itself. */
export const CitationOpenContext = createContext<(index: number, opener: HTMLElement) => void>(() => {});

interface MdNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
}

const LEAVES = new Set(["code", "inlineCode", "link", "linkReference", "definition", "html"]);

/** Turns "[n]" in text into a link to citation n, for the numbers the answer actually cites. */
function citationMarkers(cited: Set<number>) {
  const rewrite = (node: MdNode) => {
    if (!node.children || LEAVES.has(node.type)) return;
    const next: MdNode[] = [];
    for (const child of node.children) {
      if (child.type !== "text" || !child.value) {
        rewrite(child);
        next.push(child);
        continue;
      }
      let last = 0;
      for (const match of child.value.matchAll(/\[(\d{1,3})\]/g)) {
        const n = Number(match[1]);
        if (!cited.has(n)) continue;
        // The marker hugs the word it follows.
        const before = child.value.slice(last, match.index).replace(/\s+$/, "");
        if (before) next.push({ type: "text", value: before });
        next.push({ type: "link", url: `${CITE}${n}`, children: [{ type: "text", value: String(n) }] });
        last = match.index! + match[0].length;
      }
      next.push(last === 0 ? child : { type: "text", value: child.value.slice(last) });
    }
    node.children = next;
  };
  return () => rewrite;
}

function Link({ href, children }: ComponentProps<"a">) {
  const open = useContext(CitationOpenContext);
  if (href?.startsWith(CITE)) {
    const n = Number(href.slice(CITE.length));
    return (
      <button type="button" className="cite" aria-label={`Open citation ${n}`} onClick={(e) => open(n, e.currentTarget)}>
        {n}
      </button>
    );
  }
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent-text underline underline-offset-2">
      {children}
    </a>
  );
}

const heading = (tag: "h1" | "h2" | "h3" | "h4", style: string) =>
  function Heading({ children }: ComponentProps<"h1">) {
    const Tag = tag;
    return <Tag className={`font-display font-semibold text-fg first:mt-0 ${style}`}>{children}</Tag>;
  };

const COMPONENTS: Components = {
  a: Link,
  h1: heading("h1", "mb-3 mt-8 text-xl"),
  h2: heading("h2", "mb-3 mt-7 text-lg"),
  h3: heading("h3", "mb-2 mt-6 text-md"),
  h4: heading("h4", "mb-2 mt-5 text-md italic"),
  p: ({ children }) => <p className="my-4 first:mt-0 last:mb-0 [overflow-wrap:anywhere]">{children}</p>,
  ul: ({ children }) => <ul className="my-4 list-disc space-y-1.5 pl-6 marker:text-subtle">{children}</ul>,
  ol: ({ children }) => <ol className="my-4 list-decimal space-y-1.5 pl-6 marker:text-subtle">{children}</ol>,
  li: ({ children }) => <li className="[overflow-wrap:anywhere]">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="my-5 border-l-2 border-accent/50 pl-4 italic text-muted">{children}</blockquote>
  ),
  hr: () => <hr className="my-8 border-line-strong" />,
  // Wide tables and code scroll inside the reading column and never widen the page.
  table: ({ children }) => (
    <div className="my-5 overflow-x-auto rounded-control border border-line bg-surface">
      <table className="w-full border-collapse font-sans text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-sunken/60">{children}</thead>,
  th: ({ children }) => <th className="min-w-[7rem] border-b border-line-strong px-3 py-2 text-left font-semibold">{children}</th>,
  td: ({ children }) => <td className="min-w-[7rem] border-t border-line px-3 py-2 align-top">{children}</td>,
  pre: ({ children }) => (
    <pre className="my-5 overflow-x-auto rounded-panel bg-code-bg p-4 text-sm leading-relaxed text-code-fg [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-inherit">
      {children}
    </pre>
  ),
  code: ({ children }) => <code className="rounded-sm bg-sunken px-1 py-0.5 font-mono text-[0.88em]">{children}</code>,
  img: ({ alt }) => <span className="text-muted">[image{alt ? `: ${alt}` : ""}]</span>,
};

const urlTransform = (url: string) => (url.startsWith(CITE) ? url : defaultUrlTransform(url));

export function Markdown({ text, citedIndexes }: { text: string; citedIndexes: number[] }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, citationMarkers(new Set(citedIndexes))]}
      components={COMPONENTS}
      urlTransform={urlTransform}
    >
      {text}
    </ReactMarkdown>
  );
}
