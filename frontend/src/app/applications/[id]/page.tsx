"use client";
import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import {
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "@/components/ui";
import { applicationsApi } from "@/lib/api/resources";
import type { ApplicationAskResponse } from "@/lib/api/types";

interface Turn {
  query: string;
  response?: ApplicationAskResponse;
  error?: string;
}

export default function ApplicationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const app = useQuery({ queryKey: ["app", id], queryFn: () => applicationsApi.get(id) });
  const [turns, setTurns] = useState<Turn[]>([]);
  const [query, setQuery] = useState("");

  const ask = useMutation({
    mutationFn: (q: string) => applicationsApi.ask(id, q),
    onSuccess: (data, q) =>
      setTurns((t) => t.map((turn) => (turn.query === q && !turn.response ? { ...turn, response: data } : turn))),
    onError: (e, q) =>
      setTurns((t) =>
        t.map((turn) =>
          turn.query === q && !turn.response ? { ...turn, error: (e as Error).message } : turn,
        ),
      ),
  });

  if (app.isLoading) return <LoadingState />;
  if (app.isError) return <ErrorState message={(app.error as Error).message} />;
  const a = app.data!;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    setTurns((t) => [...t, { query }]);
    ask.mutate(query);
    setQuery("");
  };

  return (
    <div>
      <Link href="/applications" className="text-sm text-brand-600">
        ← RAG Applications
      </Link>
      <PageHeader title={a.name} description={a.description || undefined} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <section className="card p-5 lg:col-span-1">
          <h2 className="mb-3 text-sm font-semibold text-slate-700">Configuration</h2>
          <dl className="space-y-2 text-sm">
            <Row label="Strategy" value={<StatusBadge status={a.rag_strategy} />} />
            <Row label="Model" value={a.model_configuration?.llm_model ?? "—"} />
            <Row label="Provider" value={a.model_configuration?.llm_provider ?? "—"} />
            <Row label="Embedding" value={a.model_configuration?.embedding_model ?? "—"} />
            <Row label="Retrieval" value={a.retrieval_configuration?.mode ?? "—"} />
            <Row label="Top K" value={a.retrieval_configuration?.top_k ?? "—"} />
            <Row
              label="Citations"
              value={a.citations_enabled ? "Enabled" : "Disabled"}
            />
          </dl>
          <h3 className="mb-2 mt-5 text-xs font-semibold uppercase text-slate-400">
            Knowledge Bases
          </h3>
          <ul className="space-y-1 text-sm">
            {a.knowledge_bases.length ? (
              a.knowledge_bases.map((kb) => (
                <li key={kb.id} className="text-slate-600">
                  {kb.name}
                </li>
              ))
            ) : (
              <li className="text-slate-400">None attached</li>
            )}
          </ul>
        </section>

        <section className="card flex flex-col lg:col-span-2" style={{ minHeight: 480 }}>
          <div className="border-b border-slate-100 px-5 py-3 text-sm font-semibold text-slate-700">
            RAG Playground
          </div>
          <div className="flex-1 space-y-4 overflow-y-auto p-5">
            {turns.length === 0 && (
              <p className="text-sm text-slate-400">
                Ask a question to test retrieval and generation against this application&apos;s
                configuration.
              </p>
            )}
            {turns.map((turn, i) => (
              <div key={i} className="space-y-2">
                <div className="flex justify-end">
                  <div className="max-w-[80%] rounded-2xl bg-brand-600 px-4 py-2 text-sm text-white">
                    {turn.query}
                  </div>
                </div>
                {turn.error && (
                  <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
                    Unable to generate an answer. {turn.error}
                  </div>
                )}
                {turn.response && (
                  <div className="max-w-[90%] space-y-3">
                    <div className="rounded-2xl bg-slate-100 px-4 py-2 text-sm text-slate-800">
                      {turn.response.answer}
                    </div>
                    {turn.response.sources.length > 0 && (
                      <details className="rounded-lg border border-slate-200 bg-white p-3 text-xs">
                        <summary className="cursor-pointer font-medium text-slate-600">
                          {turn.response.sources.length} sources ·{" "}
                          {turn.response.search_mode} retrieval
                        </summary>
                        <ul className="mt-2 space-y-2">
                          {turn.response.sources.map((s, j) => (
                            <li key={j} className="border-t border-slate-100 pt-2">
                              <div className="flex justify-between text-slate-500">
                                <span className="font-medium text-slate-700">
                                  {s.document_title || "Untitled"}
                                  {s.section_title ? ` · ${s.section_title}` : ""}
                                </span>
                                <span>score {s.score.toFixed(3)}</span>
                              </div>
                              <p className="mt-1 line-clamp-3 text-slate-500">
                                {s.chunk_text}
                              </p>
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </div>
                )}
              </div>
            ))}
            {ask.isPending && <LoadingState label="Generating…" />}
          </div>
          <form onSubmit={submit} className="flex gap-2 border-t border-slate-100 p-4">
            <input
              className="input"
              placeholder="Ask a question…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button className="btn btn-primary" disabled={ask.isPending || !query.trim()}>
              Send
            </button>
          </form>
        </section>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-slate-400">{label}</dt>
      <dd className="font-medium text-slate-700">{value}</dd>
    </div>
  );
}
