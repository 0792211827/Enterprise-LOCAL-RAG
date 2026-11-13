"use client";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { EmptyState, ErrorState, LoadingState, MetricCard, PageHeader } from "@/components/ui";
import { chatAdminApi } from "@/lib/api/resources";

export default function ChatFeedbackPage() {
  const [applicationId, setApplicationId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const counts = useQuery({ queryKey: ["chat-admin", "counts"], queryFn: chatAdminApi.counts });
  const summary = useQuery({ queryKey: ["chat-admin", "summary"], queryFn: chatAdminApi.summary });
  const ratings = useQuery({
    queryKey: ["chat-admin", "ratings", applicationId],
    queryFn: () => chatAdminApi.ratings(applicationId || undefined),
  });
  const shared = useQuery({
    queryKey: ["chat-admin", "shared", applicationId],
    queryFn: () => chatAdminApi.shared(applicationId || undefined),
  });

  const exportCandidates = async () => {
    setError(null);
    try {
      const { blob, filename } = await chatAdminApi.exportGolden(applicationId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename ?? "golden-candidates.yaml";
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  if (counts.isLoading || summary.isLoading) return <LoadingState />;
  if (counts.isError) return <ErrorState message={(counts.error as Error).message} />;
  if (summary.isError) return <ErrorState message={(summary.error as Error).message} />;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Chat feedback"
        description="Ratings from employees. Conversations are private: you see counts and ratings, and only the exchanges an employee chose to share."
        action={
          <select
            className="input w-64"
            value={applicationId}
            onChange={(e) => setApplicationId(e.target.value)}
            aria-label="Assistant"
          >
            <option value="">All assistants</option>
            {counts.data?.by_application.map((a) => (
              <option key={a.application_id} value={a.application_id}>
                {a.application_name}
              </option>
            ))}
          </select>
        }
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <MetricCard label="Conversations" value={counts.data?.conversations ?? 0} hint="Counts only; never their content" />
        <MetricCard label="Messages" value={counts.data?.messages ?? 0} />
        <MetricCard
          label="Ratings"
          value={summary.data?.reduce((n, a) => n + a.up + a.down, 0) ?? 0}
          hint={`${summary.data?.reduce((n, a) => n + a.down, 0) ?? 0} of them 👎`}
        />
      </div>

      <section className="card p-5">
        <h2 className="mb-3 text-sm font-semibold text-slate-700">Ratings per assistant</h2>
        {!summary.data?.length ? (
          <p className="text-sm text-slate-400">No ratings yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-slate-400">
              <tr>
                <th className="py-1">Assistant</th>
                <th>👍</th>
                <th>👎</th>
              </tr>
            </thead>
            <tbody>
              {summary.data.map((a) => (
                <tr key={a.application_id} className="border-t border-slate-100">
                  <td className="py-2">{a.application_name}</td>
                  <td>{a.up}</td>
                  <td>{a.down}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card p-5">
        <h2 className="mb-3 text-sm font-semibold text-slate-700">Ratings</h2>
        {ratings.isLoading && <LoadingState />}
        {ratings.isError && <ErrorState message={(ratings.error as Error).message} />}
        {ratings.data && ratings.data.length === 0 && <p className="text-sm text-slate-400">No ratings yet.</p>}
        {!!ratings.data?.length && (
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-slate-400">
              <tr>
                <th className="py-1">Message</th>
                <th>Rating</th>
                <th>Comment</th>
                <th>Shared</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {ratings.data.map((r) => (
                <tr key={r.message_id} className="border-t border-slate-100 align-top">
                  <td className="py-2 font-mono text-xs text-slate-500">{r.message_id.slice(0, 8)}</td>
                  <td>{r.rating === "up" ? "👍" : "👎"}</td>
                  <td className="whitespace-pre-wrap">{r.comment ?? <span className="text-slate-300">—</span>}</td>
                  <td>{r.shared ? "Yes" : "No"}</td>
                  <td className="text-xs text-slate-500">{new Date(r.rated_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card p-5">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">Shared exchanges</h2>
          <button
            className="btn btn-secondary"
            onClick={exportCandidates}
            disabled={!applicationId}
            title={applicationId ? undefined : "Choose an assistant to export its shared exchanges"}
          >
            Export as Golden set candidates
          </button>
        </div>
        {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
        {shared.isLoading && <LoadingState />}
        {shared.isError && <ErrorState message={(shared.error as Error).message} />}
        {shared.data?.length === 0 && (
          <EmptyState
            title="Nothing shared"
            description="Employees can share an exchange with admins when they rate an answer."
          />
        )}
        <div className="space-y-3">
          {shared.data?.map((x) => (
            <div key={x.message_id} className="rounded-lg border border-slate-200 p-3 text-sm">
              <div className="text-xs text-slate-400">
                {x.rating === "up" ? "👍" : "👎"} {x.comment && <span className="text-slate-600">“{x.comment}”</span>}
              </div>
              <div className="mt-2 font-medium text-slate-800">{x.question}</div>
              <div className="mt-1 whitespace-pre-wrap text-slate-600">{x.answer}</div>
              {x.sources.length > 0 && <div className="mt-2 text-xs text-slate-400">Sources: {x.sources.join(", ")}</div>}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
