"use client";
import { FileText, OctagonAlert, RotateCw } from "lucide-react";
import { useLayoutEffect, useRef } from "react";
import { CopyButton, Icon } from "@/components/ui";
import type { ChatApplication, ChatAttachment, ChatCitation } from "@/lib/api/types";
import { AttachmentChips } from "./AttachmentChips";
import { CitationOpenContext, Markdown } from "./Markdown";
import type { Turn } from "./useConversation";

/** The reading column: a question as a heading, its answer as prose beneath, no bubbles and no avatars. */
export function MessageStream({
  turns,
  conversationId,
  busy,
  application,
  resetKey,
  onCite,
  onRetry,
  onDetach,
}: {
  turns: Turn[];
  conversationId: string | null;
  busy: boolean;
  application: ChatApplication | undefined;
  /** Changes when another Conversation is shown, so the view jumps to its latest message. */
  resetKey: number;
  onCite: (citations: ChatCitation[], index: number, opener: HTMLElement) => void;
  onRetry: () => void;
  onDetach: (attachment: ChatAttachment) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  // Follow the answer as it grows, but only while the reader is at the bottom: scrolling up to reread
  // must not be undone by the next word arriving.
  const following = useRef(true);
  const count = useRef(0);

  useLayoutEffect(() => {
    following.current = true;
  }, [resetKey]);
  useLayoutEffect(() => {
    const last = turns[turns.length - 1];
    // A new message, or an answer starting over after a retry: show it.
    if (turns.length !== count.current || (last?.streaming && !last.text)) following.current = true;
    count.current = turns.length;
    const el = scroller.current;
    if (el && following.current) el.scrollTop = el.scrollHeight;
  }, [turns, resetKey]);

  const onScroll = () => {
    const el = scroller.current;
    if (el) following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 96;
  };

  return (
    <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto" aria-label="Conversation">
      <div className="mx-auto w-full max-w-[42rem] px-4 py-8 sm:px-6">
        {turns.length === 0 && <Welcome application={application} />}
        {turns.map((turn, i) =>
          turn.role === "user" ? (
            <Question key={`${resetKey}-${i}`} turn={turn} first={i === 0} onDetach={conversationId ? onDetach : undefined} />
          ) : (
            <Answer
              key={`${resetKey}-${i}`}
              turn={turn}
              canRetry={i === turns.length - 1 && !busy}
              onCite={onCite}
              onRetry={onRetry}
            />
          ),
        )}
      </div>
    </div>
  );
}

function Welcome({ application }: { application: ChatApplication | undefined }) {
  return (
    <div className="py-16 text-center sm:py-24">
      <h2 className="font-display text-2xl font-semibold text-fg sm:text-3xl">
        {application ? `Ask ${application.name}` : "Ask a question"}
      </h2>
      <p className="mx-auto mt-3 max-w-md text-muted">
        {application?.description || "Answers come with the sources they are drawn from, so you can check them."}
      </p>
    </div>
  );
}

function Question({
  turn,
  first,
  onDetach,
}: {
  turn: Turn;
  first: boolean;
  onDetach?: (attachment: ChatAttachment) => void;
}) {
  return (
    <div className={first ? "" : "mt-10 border-t border-line pt-8"}>
      <p className="whitespace-pre-wrap font-display text-lg font-semibold leading-snug text-fg [overflow-wrap:anywhere]">
        {turn.text}
      </p>
      {!!turn.attachments?.length && (
        <div className="mt-3">
          <AttachmentChips attachments={turn.attachments} onRemove={onDetach} />
        </div>
      )}
    </div>
  );
}

function Answer({
  turn,
  canRetry,
  onCite,
  onRetry,
}: {
  turn: Turn;
  canRetry: boolean;
  onCite: (citations: ChatCitation[], index: number, opener: HTMLElement) => void;
  onRetry: () => void;
}) {
  const thinking = turn.streaming && !turn.text && !turn.error;
  const settled = !turn.streaming;
  return (
    <div className="mt-5">
      {thinking && (
        <p role="status" className="flex items-center gap-2 font-display text-md italic text-subtle">
          <span aria-hidden className="flex gap-1">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-subtle" />
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-subtle [animation-delay:200ms]" />
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-subtle [animation-delay:400ms]" />
          </span>
          Thinking…
        </p>
      )}
      {turn.text && (
        <div className="reading max-w-none">
          <CitationOpenContext.Provider value={(index, opener) => onCite(turn.citations, index, opener)}>
            <Markdown text={turn.text} citedIndexes={turn.citations.map((c) => c.index)} />
          </CitationOpenContext.Provider>
        </div>
      )}
      {turn.stopped && <p className="mt-3 text-sm text-subtle">Stopped</p>}
      {turn.error && (
        <div
          role="alert"
          className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-control border border-danger/30 bg-danger-soft px-3 py-2.5 text-sm text-fg"
        >
          <Icon icon={OctagonAlert} className="text-danger" />
          <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{turn.error}</span>
          {canRetry && (
            <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}>
              <Icon icon={RotateCw} size="sm" />
              Retry
            </button>
          )}
        </div>
      )}
      {turn.stopped && !turn.error && canRetry && turn.text === "" && (
        <button type="button" className="btn btn-secondary btn-sm mt-2" onClick={onRetry}>
          <Icon icon={RotateCw} size="sm" />
          Retry
        </button>
      )}
      {turn.citations.length > 0 && <CitationList citations={turn.citations} onCite={onCite} />}
      {settled && turn.text && (
        <div className="mt-4 flex flex-wrap items-start gap-x-2 gap-y-1 font-sans">
          <CopyButton quiet label="Copy answer" value={turn.text} />
        </div>
      )}
    </div>
  );
}

function CitationList({
  citations,
  onCite,
}: {
  citations: ChatCitation[];
  onCite: (citations: ChatCitation[], index: number, opener: HTMLElement) => void;
}) {
  return (
    <ul aria-label="Citations" className="mt-5 flex flex-col gap-1.5 font-sans">
      {citations.map((c) => (
        <li key={c.index}>
          <button
            type="button"
            onClick={(e) => onCite(citations, c.index, e.currentTarget)}
            className="group flex w-full items-baseline gap-2.5 rounded-control px-2 py-1.5 text-left text-sm hover:bg-sunken"
          >
            <span className="inline-flex h-5 min-w-[1.25rem] shrink-0 translate-y-0.5 items-center justify-center rounded-sm bg-accent-soft px-1 text-xs font-semibold text-accent-text">
              {c.index}
            </span>
            <span className="min-w-0 flex-1">
              <span className="font-medium text-fg [overflow-wrap:anywhere]">{c.title || "Untitled document"}</span>
              {c.attachment_id ? (
                <span className="ml-2 inline-flex items-center gap-1 rounded-sm bg-clay-soft px-1.5 py-0.5 text-xs font-medium text-clay-text">
                  <Icon icon={FileText} size="sm" />
                  Your file
                </span>
              ) : (
                <>
                  {c.section_title && <span className="text-muted"> · {c.section_title}</span>}
                  {c.locator_text && <span className="text-muted"> · {c.locator_text}</span>}
                </>
              )}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
