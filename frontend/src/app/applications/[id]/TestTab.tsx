"use client";
import { useMutation } from "@tanstack/react-query";
import { FileText, SendHorizontal } from "lucide-react";
import { useState } from "react";
import { EmptyState, ErrorState, Icon, SkeletonText } from "@/components/ui";
import { applicationsApi } from "@/lib/api/resources";
import type { Application, ApplicationAskResponse } from "@/lib/api/types";
import { Markdown } from "../../chat/Markdown";

interface Turn {
  query: string;
  response?: ApplicationAskResponse;
  error?: string;
}

/** Ask the Application a question as an Admin and see what it retrieved, to check changes before anyone else does. */
export function TestTab({ app }: { app: Application }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [query, setQuery] = useState("");

  const ask = useMutation({
    mutationFn: (q: string) => applicationsApi.ask(app.id, q),
    onSuccess: (data, q) => setTurns((t) => t.map((turn) => (turn.query === q && !turn.response && !turn.error ? { ...turn, response: data } : turn))),
    onError: (e, q) => setTurns((t) => t.map((turn) => (turn.query === q && !turn.response && !turn.error ? { ...turn, error: (e as Error).message } : turn))),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q || ask.isPending) return;
    setTurns((t) => [...t, { query: q }]);
    ask.mutate(q);
    setQuery("");
  };

  return (
    <div className="max-w-3xl">
      <div className="space-y-6">
        {turns.length === 0 && (
          <EmptyState
            title="Ask a test question"
            description="You will see the answer, the passages it was built from, and how they were found. This does not appear in anyone's Conversations."
          />
        )}
        {turns.map((turn, i) => (
          <article key={i} aria-label={`Test question ${i + 1}`} className="space-y-3">
            <h3 className="font-display text-md font-semibold text-fg">{turn.query}</h3>
            {!turn.response && !turn.error && (
              <div role="status">
                <span className="sr-only">Generating an answer</span>
                <SkeletonText lines={3} />
              </div>
            )}
            {turn.error && <ErrorState title="No answer" message={turn.error} />}
            {turn.response && (
              <>
                <div className="card p-4 text-sm leading-relaxed">
                  <Markdown text={turn.response.answer} citedIndexes={[]} />
                </div>
                <details open className="rounded-panel border border-line bg-surface text-sm">
                  <summary className="cursor-pointer select-none px-4 py-2.5 font-medium">
                    Retrieval detail: {turn.response.sources.length} source{turn.response.sources.length === 1 ? "" : "s"}, {turn.response.search_mode} search, {turn.response.chunks_used} passage
                    {turn.response.chunks_used === 1 ? "" : "s"} used
                  </summary>
                  {turn.response.sources.length === 0 ? (
                    <p className="border-t border-line px-4 py-3 text-muted">Nothing was retrieved for this question, so the answer is not grounded in any Document.</p>
                  ) : (
                    <ol aria-label="Citations" className="divide-y divide-line border-t border-line">
                      {turn.response.sources.map((s, j) => (
                        <li key={j} className="px-4 py-3">
                          <div className="flex flex-wrap items-baseline justify-between gap-2">
                            <span className="flex items-center gap-2 font-medium text-fg">
                              <span className="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-sm bg-accent-soft px-1 text-xs font-semibold text-accent-text">{j + 1}</span>
                              <Icon icon={FileText} size="sm" className="text-subtle" />
                              {s.document_title || "Untitled document"}
                              {s.section_title ? <span className="font-normal text-muted"> · {s.section_title}</span> : null}
                            </span>
                            <span className="font-mono text-xs text-muted">
                              score {s.score.toFixed(3)}
                              {s.retrieval_method ? ` · ${s.retrieval_method}` : ""}
                            </span>
                          </div>
                          <p className="mt-1.5 line-clamp-4 text-muted">{s.chunk_text}</p>
                        </li>
                      ))}
                    </ol>
                  )}
                </details>
              </>
            )}
          </article>
        ))}
      </div>

      <form onSubmit={submit} className="mt-6 flex gap-2">
        <label htmlFor="test-question" className="sr-only">
          Test question
        </label>
        <input id="test-question" className="input" placeholder="Ask a question…" value={query} onChange={(e) => setQuery(e.target.value)} />
        <button type="submit" className="btn btn-primary" disabled={ask.isPending || !query.trim()}>
          <Icon icon={SendHorizontal} size="sm" />
          Ask
        </button>
      </form>
    </div>
  );
}
