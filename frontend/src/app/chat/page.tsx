"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, ReactNode, useEffect, useRef, useState } from "react";
import { EmptyState, ErrorState, LoadingState, Modal, PageHeader, confirmDialog } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { chatApi } from "@/lib/api/resources";
import type { ChatCitation, ConversationSummary, MessageFeedback } from "@/lib/api/types";

interface Turn {
  id?: string; // the saved message's id; known once an answer is complete
  role: "user" | "assistant";
  text: string;
  citations: ChatCitation[];
  feedback?: MessageFeedback | null;
  error?: string;
  streaming?: boolean;
}

export default function ChatPage() {
  const queryClient = useQueryClient();
  const apps = useQuery({ queryKey: ["chat", "applications"], queryFn: chatApi.applications });
  const history = useQuery({ queryKey: ["chat", "conversations"], queryFn: chatApi.conversations });
  const [appId, setAppId] = useState<string>("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [cited, setCited] = useState<ChatCitation | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);

  // Default to the first Application offered; keep the choice while it is still offered.
  useEffect(() => {
    if (apps.data && !apps.data.some((a) => a.id === appId)) setAppId(apps.data[0]?.id ?? "");
  }, [apps.data, appId]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);
  useEffect(() => {
    return () => abort.current?.abort();
  }, []);

  const refreshHistory = () => queryClient.invalidateQueries({ queryKey: ["chat", "conversations"] });

  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => chatApi.renameConversation(id, title),
    onSuccess: refreshHistory,
  });
  const remove = useMutation({
    mutationFn: (id: string) => chatApi.deleteConversation(id),
    onSuccess: (_, id) => {
      if (id === conversationId) startNew();
      refreshHistory();
    },
  });

  if (apps.isLoading || history.isLoading) return <LoadingState />;
  if (apps.isError) return <ErrorState message={(apps.error as Error).message} />;
  if (!apps.data?.length) {
    return (
      <div>
        <PageHeader title="Chat" />
        <EmptyState
          title="No assistants available to you yet"
          description="You can use an assistant once you have been given access to all the knowledge it answers from. Ask an administrator."
        />
      </div>
    );
  }

  const application = apps.data.find((a) => a.id === appId);
  const patchLast = (change: (turn: Turn) => Turn) =>
    setTurns((all) => all.map((turn, i) => (i === all.length - 1 ? change(turn) : turn)));

  function startNew() {
    abort.current?.abort();
    setConversationId(null);
    setTurns([]);
    setBusy(false);
    setOpenError(null);
  }

  const choose = (id: string) => {
    startNew();
    setAppId(id);
  };

  const open = async (summary: ConversationSummary) => {
    abort.current?.abort();
    setBusy(false);
    setOpenError(null);
    try {
      const detail = await chatApi.conversation(summary.id);
      setConversationId(detail.id);
      setAppId(detail.application_id);
      setTurns(
        detail.messages.map((m) => ({
          id: m.id,
          role: m.role,
          text: m.content,
          citations: m.citations,
          feedback: m.feedback,
        })),
      );
    } catch (err) {
      setOpenError((err as Error).message);
    }
  };

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const query = draft.trim();
    if (!query || !application || busy) return;
    setDraft("");
    setBusy(true);
    setTurns((all) => [
      ...all,
      { role: "user", text: query, citations: [] },
      { role: "assistant", text: "", citations: [], streaming: true },
    ]);
    const controller = new AbortController();
    abort.current = controller;
    try {
      let id = conversationId;
      if (!id) {
        id = (await chatApi.startConversation(application.id)).id;
        setConversationId(id);
      }
      await chatApi.ask(
        id,
        query,
        (event) => {
          if (event.type === "delta") patchLast((t) => ({ ...t, text: t.text + event.content }));
          if (event.type === "citations") patchLast((t) => ({ ...t, citations: event.citations }));
          if (event.type === "error") patchLast((t) => ({ ...t, error: event.message }));
          if (event.type === "done") patchLast((t) => ({ ...t, id: event.message_id ?? undefined }));
        },
        controller.signal,
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        const message =
          err instanceof ApiError && err.status === 404
            ? "This assistant is no longer available to you."
            : (err as Error).message;
        patchLast((t) => ({ ...t, error: message }));
      }
    } finally {
      patchLast((t) => ({ ...t, streaming: false }));
      setBusy(false);
      refreshHistory();
    }
  };

  const onRename = (c: ConversationSummary) => {
    const title = window.prompt("Rename this conversation", c.title || "Untitled");
    if (title && title.trim() && title.trim() !== c.title) rename.mutate({ id: c.id, title: title.trim() });
  };
  const onDelete = (c: ConversationSummary) => {
    if (confirmDialog(`Delete "${c.title || "Untitled"}"? This removes its messages for good.`)) remove.mutate(c.id);
  };

  return (
    <div className="flex h-[calc(100vh-4rem)] gap-4">
      <aside className="card flex w-64 shrink-0 flex-col overflow-hidden" aria-label="Conversation history">
        <div className="border-b border-slate-200 p-3">
          <button className="btn btn-primary w-full" onClick={startNew}>
            New chat
          </button>
        </div>
        <ul className="flex-1 overflow-y-auto p-2">
          {history.data?.length === 0 && (
            <li className="px-2 py-6 text-center text-xs text-slate-400">Your conversations will appear here.</li>
          )}
          {history.data?.map((c) => (
            <li
              key={c.id}
              className={`group mb-1 flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm ${
                c.id === conversationId ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-50"
              }`}
            >
              <button className="min-w-0 flex-1 truncate text-left" onClick={() => open(c)} title={c.title}>
                {c.title || "Untitled"}
              </button>
              <button
                className="hidden text-xs text-slate-400 hover:text-slate-700 group-hover:block"
                onClick={() => onRename(c)}
                aria-label={`Rename ${c.title || "conversation"}`}
              >
                ✎
              </button>
              <button
                className="hidden text-xs text-slate-400 hover:text-red-600 group-hover:block"
                onClick={() => onDelete(c)}
                aria-label={`Delete ${c.title || "conversation"}`}
              >
                🗑
              </button>
            </li>
          ))}
        </ul>
        <p className="border-t border-slate-200 p-3 text-xs text-slate-400">
          Only you can read your conversations. They are deleted automatically after a retention period.
        </p>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader
          title="Chat"
          description="Ask a question and get an answer from your company's documents."
          action={
            <select
              className="input w-64"
              value={appId}
              onChange={(e) => choose(e.target.value)}
              disabled={!!conversationId}
              title={conversationId ? "Start a new chat to use a different assistant" : undefined}
              aria-label="Assistant"
            >
              {apps.data.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          }
        />
        {application?.description && <p className="-mt-3 mb-4 text-sm text-slate-500">{application.description}</p>}
        {openError && <p className="mb-3 text-sm text-red-600">{openError}</p>}
        {conversationId && !application && (
          <p className="mb-3 text-sm text-amber-700">
            You can read this conversation, but its assistant is no longer available to you.
          </p>
        )}

        <div className="card flex-1 space-y-4 overflow-y-auto p-5">
          {turns.length === 0 && (
            <p className="py-10 text-center text-sm text-slate-400">
              Ask {application?.name ?? "the assistant"} a question to get started.
            </p>
          )}
          {turns.map((turn, i) => (
            <Bubble
              key={turn.id ?? i}
              turn={turn}
              onCite={setCited}
              conversationId={conversationId}
              onRated={(feedback) =>
                setTurns((all) => all.map((t) => (t.id && t.id === turn.id ? { ...t, feedback } : t)))
              }
            />
          ))}
          <div ref={bottom} />
        </div>

        <form onSubmit={send} className="mt-4 flex gap-3">
          <input
            className="input flex-1"
            placeholder="Ask a question…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={4000}
            disabled={busy || !application}
            aria-label="Your question"
          />
          <button className="btn btn-primary" disabled={busy || !draft.trim() || !application}>
            {busy ? "Answering…" : "Send"}
          </button>
        </form>
      </div>

      <SourceModal citation={cited} onClose={() => setCited(null)} />
    </div>
  );
}

function Bubble({
  turn,
  onCite,
  conversationId,
  onRated,
}: {
  turn: Turn;
  onCite: (c: ChatCitation) => void;
  conversationId: string | null;
  onRated: (feedback: MessageFeedback | null) => void;
}) {
  const mine = turn.role === "user";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm ${
          mine ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-800"
        }`}
      >
        <div className="whitespace-pre-wrap">
          {turn.text || (turn.streaming && !turn.error ? <span className="text-slate-400">Thinking…</span> : null)}
        </div>
        {turn.error && <div className="mt-2 text-xs text-red-600">{turn.error}</div>}
        {turn.citations.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2 border-t border-slate-200 pt-2">
            {turn.citations.map((c) => (
              <button
                key={c.index}
                onClick={() => onCite(c)}
                className="rounded-md bg-white px-2 py-1 text-xs text-brand-700 ring-1 ring-slate-200 hover:bg-brand-50"
                title={c.passage}
              >
                [{c.index}] {c.title || "Source"}
              </button>
            ))}
          </div>
        )}
        {!mine && turn.id && !turn.streaming && conversationId && (
          <FeedbackBar conversationId={conversationId} messageId={turn.id} feedback={turn.feedback ?? null} onRated={onRated} />
        )}
      </div>
    </div>
  );
}

/** Thumbs, an optional comment, and the explicit choice to share this one exchange with admins. */
function FeedbackBar({
  conversationId,
  messageId,
  feedback,
  onRated,
}: {
  conversationId: string;
  messageId: string;
  feedback: MessageFeedback | null;
  onRated: (feedback: MessageFeedback | null) => void;
}) {
  const [editing, setEditing] = useState<"up" | "down" | null>(null);
  const [comment, setComment] = useState(feedback?.comment ?? "");
  const [share, setShare] = useState(feedback?.share_with_admins ?? false);
  const [error, setError] = useState<string | null>(null);

  const begin = (rating: "up" | "down") => {
    setComment(feedback?.comment ?? "");
    setShare(feedback?.share_with_admins ?? false);
    setError(null);
    setEditing(rating);
  };
  const save = async () => {
    if (!editing) return;
    try {
      const saved = await chatApi.rate(conversationId, messageId, {
        rating: editing,
        comment: comment.trim() || null,
        share_with_admins: share,
      });
      onRated(saved);
      setEditing(null);
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const withdraw = async () => {
    try {
      await chatApi.withdrawRating(conversationId, messageId);
      onRated(null);
      setEditing(null);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const thumb = (rating: "up" | "down", label: string, icon: string) => (
    <button
      onClick={() => begin(rating)}
      aria-label={label}
      aria-pressed={feedback?.rating === rating}
      className={`rounded-md px-2 py-0.5 text-sm ring-1 ring-slate-200 hover:bg-white ${
        feedback?.rating === rating ? "bg-white font-semibold text-brand-700" : "text-slate-500"
      }`}
    >
      {icon}
    </button>
  );

  return (
    <div className="mt-3 border-t border-slate-200 pt-2">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        {thumb("up", "Good answer", "👍")}
        {thumb("down", "Bad answer", "👎")}
        {feedback?.share_with_admins && <span>Shared with admins</span>}
      </div>
      {editing && (
        <div className="mt-2 space-y-2">
          <textarea
            className="input h-16 w-full"
            placeholder="Add a comment (optional) — admins will see it"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            maxLength={2000}
            aria-label="Comment"
          />
          <label className="flex items-start gap-2 text-xs text-slate-600">
            <input type="checkbox" className="mt-0.5" checked={share} onChange={(e) => setShare(e.target.checked)} />
            <span>
              Share this exchange with admins. They will see this question and answer, and nothing else from your
              conversations.
            </span>
          </label>
          {error && <div className="text-xs text-red-600">{error}</div>}
          <div className="flex gap-2">
            <button className="btn btn-primary" onClick={save}>
              Save {editing === "up" ? "👍" : "👎"}
            </button>
            {feedback && (
              <button className="btn btn-secondary" onClick={withdraw}>
                Remove rating
              </button>
            )}
            <button className="btn btn-secondary" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Opens the cited Document and shows the cited passage in it. */
function SourceModal({ citation, onClose }: { citation: ChatCitation | null; onClose: () => void }) {
  const doc = useQuery({
    queryKey: ["chat", "document", citation?.document_id],
    queryFn: () => chatApi.document(citation!.document_id!),
    enabled: !!citation?.document_id,
    retry: false,
  });
  return (
    <Modal open={!!citation} onClose={onClose} title={citation?.title || "Source"} size="xl">
      {citation && (
        <div className="space-y-4 text-sm">
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
              Cited passage{citation.section_title ? ` — ${citation.section_title}` : ""}
            </div>
            <blockquote className="rounded-lg border-l-4 border-brand-500 bg-brand-50 p-3 text-slate-700">
              {citation.passage}
            </blockquote>
          </div>
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Document</div>
            {doc.isLoading && <LoadingState />}
            {doc.isError && (
              <p className="text-slate-500">The full document is not available to you.</p>
            )}
            {doc.data && (
              <div className="max-h-80 overflow-y-auto whitespace-pre-wrap rounded-lg border border-slate-200 p-3 text-slate-600">
                {highlight(doc.data.text, citation.passage)}
              </div>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

/** The document text with the cited passage marked; if it cannot be found verbatim, the text as is. */
function highlight(text: string, passage: string): ReactNode {
  const at = passage ? text.indexOf(passage) : -1;
  if (at < 0) return text;
  return (
    <>
      {text.slice(0, at)}
      <mark className="rounded bg-amber-200 px-0.5">{passage}</mark>
      {text.slice(at + passage.length)}
    </>
  );
}
