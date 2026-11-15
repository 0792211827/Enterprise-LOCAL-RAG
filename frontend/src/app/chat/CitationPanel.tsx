"use client";
import { useQuery } from "@tanstack/react-query";
import { FileText, Lock, X } from "lucide-react";
import { ReactNode, useEffect, useRef } from "react";
import { Icon, LoadingState, Sheet } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { chatApi } from "@/lib/api/resources";
import type { ChatCitation } from "@/lib/api/types";

const LABEL = "Cited source";

/**
 * The source of one Citation beside the answer on a wide screen, or rising from the bottom on a phone.
 * Arrow keys move between the answer's Citations while focus is in it; Escape closes it.
 */
export function CitationPanel({
  citations,
  selected,
  sheet,
  onSelect,
  onClose,
}: {
  citations: ChatCitation[];
  selected: number;
  sheet: boolean;
  onSelect: (position: number) => void;
  onClose: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const citation = citations[selected];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      if (e.key === "Escape" && !sheet) {
        e.preventDefault();
        onClose();
        return;
      }
      const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
      const el = root.current;
      const active = document.activeElement;
      if (!step || !el || !active || active === document.body || active === document.documentElement) return;
      // Only while the keys are not already doing something for the person: typing, or moving in the page.
      if (!(el.contains(active) || active.contains(el))) return;
      if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) return;
      e.preventDefault();
      const next = Math.min(citations.length - 1, Math.max(0, selected + step));
      if (next !== selected) onSelect(next);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [citations.length, selected, sheet, onClose, onSelect]);

  // Focus enters the side panel when it opens (a sheet does this itself), so the keyboard works at once.
  useEffect(() => {
    if (!sheet) root.current?.closest<HTMLElement>("aside")?.focus();
  }, [sheet]);

  if (!citation) return null;
  const body = (
    <div ref={root}>
      {citations.length > 1 && (
        <div role="tablist" aria-label="Switch source" className="mb-4 flex flex-wrap gap-1.5">
          {citations.map((c, i) => (
            <button
              key={c.index}
              type="button"
              role="tab"
              aria-selected={i === selected}
              aria-label={`Source ${c.index}: ${c.title || "Source"}`}
              onClick={() => onSelect(i)}
              className={`h-7 min-w-[1.75rem] rounded-sm px-2 text-xs font-semibold transition-colors duration-fast ${
                i === selected ? "bg-accent text-accent-fg" : "bg-accent-soft text-accent-text hover:bg-accent/20"
              }`}
            >
              {c.index}
            </button>
          ))}
        </div>
      )}
      <Source key={citation.index} citation={citation} />
    </div>
  );

  if (sheet) {
    return (
      <Sheet open onClose={onClose} title={LABEL}>
        {body}
      </Sheet>
    );
  }
  return (
    <aside
      aria-label={LABEL}
      tabIndex={-1}
      className="flex h-full w-[26rem] shrink-0 flex-col border-l border-line bg-surface outline-none"
    >
      <div className="flex items-center justify-between border-b border-line px-5 py-3">
        <h2 className="text-sm font-medium text-muted">{LABEL}</h2>
        <button type="button" onClick={onClose} className="btn btn-ghost btn-sm -mr-2" aria-label="Close">
          <Icon icon={X} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">{body}</div>
    </aside>
  );
}

function Source({ citation }: { citation: ChatCitation }) {
  const own = !!citation.attachment_id;
  const doc = useQuery({
    queryKey: ["chat", "document", citation.document_id],
    queryFn: () => chatApi.document(citation.document_id!),
    enabled: !!citation.document_id && !own,
    retry: false,
  });
  const limited = doc.error instanceof ApiError && (doc.error.status === 403 || doc.error.status === 404);

  return (
    <article>
      <h3 className="font-display text-lg font-semibold leading-snug text-fg [overflow-wrap:anywhere]">
        {citation.title || "Source"}
      </h3>
      {own ? (
        <p className="mt-1.5 inline-flex items-center gap-1.5 rounded-sm bg-clay-soft px-2 py-0.5 text-xs font-medium text-clay-text">
          <Icon icon={FileText} size="sm" />
          From your file
        </p>
      ) : (
        citation.section_title && <p className="mt-1 text-sm text-muted">{citation.section_title}</p>
      )}

      <div className="mt-5">
        {own && (
          <Excerpt>
            <Passage text={citation.passage} />
          </Excerpt>
        )}
        {!own && doc.isLoading && <LoadingState label="Opening the source…" />}
        {!own && limited && (
          <>
            <div className="mb-4 flex items-start gap-2.5 rounded-control border border-line-strong bg-sunken/60 px-3 py-2.5 text-sm text-fg">
              <Icon icon={Lock} className="mt-0.5 text-subtle" />
              <p>
                <span className="font-medium">Access to this document is limited.</span> You can read the passage the
                answer used, but not the rest of the document.
              </p>
            </div>
            <blockquote className="border-l-2 border-line-strong pl-4 text-sm text-muted">{citation.passage}</blockquote>
          </>
        )}
        {!own && doc.isError && !limited && (
          <>
            <p className="mb-4 text-sm text-danger">The source couldn&apos;t be opened just now. {(doc.error as Error).message}</p>
            <blockquote className="border-l-2 border-line-strong pl-4 text-sm text-muted">{citation.passage}</blockquote>
          </>
        )}
        {!own && doc.data && <DocumentText text={doc.data.text} passage={citation.passage} />}
      </div>
    </article>
  );
}

const MARK = "rounded-sm bg-clay-scale-100 px-0.5 text-fg [box-decoration-break:clone]";
const Passage = ({ text }: { text: string }) => <mark className={MARK}>{text}</mark>;

const Excerpt = ({ children }: { children: ReactNode }) => (
  <div className="whitespace-pre-wrap rounded-control border border-line bg-canvas p-3.5 text-sm leading-relaxed text-muted [overflow-wrap:anywhere]">
    {children}
  </div>
);

/** Finds the passage in the text, forgiving of differences in whitespace. */
function locate(text: string, passage: string): [number, number] | null {
  if (!passage.trim()) return null;
  const exact = text.indexOf(passage);
  if (exact >= 0) return [exact, exact + passage.length];
  const pattern = passage
    .trim()
    .split(/\s+/)
    .map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("\\s+");
  const match = new RegExp(pattern).exec(text);
  return match ? [match.index, match.index + match[0].length] : null;
}

/** The whole document with the cited passage marked and brought into view; if it cannot be found, shown on its own. */
function DocumentText({ text, passage }: { text: string; passage: string }) {
  const box = useRef<HTMLDivElement>(null);
  const found = locate(text, passage);
  useEffect(() => {
    const mark = box.current?.querySelector("mark");
    if (box.current && mark) box.current.scrollTop = Math.max(0, mark.offsetTop - box.current.clientHeight / 3);
  }, [text, passage]);

  if (!found) {
    return (
      <div className="space-y-4">
        <Excerpt>
          <Passage text={passage} />
        </Excerpt>
        <p className="text-xs font-medium uppercase tracking-wide text-subtle">Full document</p>
        <Excerpt>{text}</Excerpt>
      </div>
    );
  }
  const [from, to] = found;
  return (
    <div ref={box} className="relative max-h-[28rem] overflow-y-auto whitespace-pre-wrap rounded-control border border-line bg-canvas p-3.5 text-sm leading-relaxed text-muted [overflow-wrap:anywhere]">
      {text.slice(0, from)}
      <Passage text={text.slice(from, to)} />
      {text.slice(to)}
    </div>
  );
}
