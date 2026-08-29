"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import {
  EmptyState,
  ErrorState,
  HealthIndicator,
  LoadingState,
  MetricCard,
  PageHeader,
  StatusBadge,
} from "@/components/ui";
import { documentsApi, systemApi } from "@/lib/api/resources";

export default function DashboardPage() {
  const stats = useQuery({ queryKey: ["stats"], queryFn: systemApi.stats });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: systemApi.health,
    refetchInterval: 15_000,
  });
  const gpu = useQuery({ queryKey: ["gpu"], queryFn: systemApi.gpu });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: documentsApi.jobs });

  if (stats.isError) return <ErrorState message={(stats.error as Error).message} />;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="Operational overview of your self-hosted Enterprise Local RAG."
      />

      {stats.isLoading ? (
        <LoadingState />
      ) : (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-5">
          <MetricCard label="Knowledge Bases" value={stats.data!.knowledge_bases} />
          <MetricCard label="Documents" value={stats.data!.documents} />
          <MetricCard label="Applications" value={stats.data!.applications} />
          <MetricCard label="Providers" value={stats.data!.providers} />
          <MetricCard label="Indexed Chunks" value={stats.data!.chunks} />
        </div>
      )}

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="card p-5">
          <h2 className="mb-4 text-sm font-semibold text-slate-700">System Health</h2>
          {health.isLoading && <LoadingState />}
          {health.isError && (
            <p className="text-sm text-red-600">
              Backend unreachable — {(health.error as Error).message}
            </p>
          )}
          {health.data && (
            <ul className="space-y-2.5">
              {health.data.components.map((c) => (
                <li key={c.name} className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2.5">
                    <HealthIndicator status={c.status} />
                    {c.name}
                  </span>
                  <span className="flex items-center gap-3 text-xs text-slate-400">
                    {c.latency_ms != null && <span>{c.latency_ms} ms</span>}
                    <StatusBadge status={c.status} />
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card p-5">
          <h2 className="mb-4 text-sm font-semibold text-slate-700">GPU</h2>
          {gpu.isLoading && <LoadingState />}
          {gpu.data && !gpu.data.available && (
            <p className="text-sm text-slate-400">
              {gpu.data.message || "GPU information unavailable"}
            </p>
          )}
          {gpu.data?.available &&
            gpu.data.gpus.map((g) => (
              <div key={g.index} className="mb-3 text-sm">
                <div className="font-medium text-slate-700">{g.name}</div>
                <div className="mt-1 text-xs text-slate-500">
                  {g.memory_used_mb ?? "?"} / {g.memory_total_mb ?? "?"} MB ·{" "}
                  {g.utilization_percent ?? "?"}% util · {g.temperature_c ?? "?"}°C
                </div>
              </div>
            ))}
        </section>
      </div>

      <section className="card mt-6 p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">Recent Ingestion Jobs</h2>
          <Link href="/documents" className="text-xs font-medium text-brand-600">
            View all →
          </Link>
        </div>
        {jobs.isLoading && <LoadingState />}
        {jobs.data && jobs.data.length === 0 && (
          <EmptyState
            title="No ingestion jobs yet"
            description="Upload a document to a knowledge base to start ingestion."
          />
        )}
        {jobs.data && jobs.data.length > 0 && (
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-slate-400">
              <tr>
                <th className="pb-2 font-medium">Job</th>
                <th className="pb-2 font-medium">Stage</th>
                <th className="pb-2 font-medium">Status</th>
                <th className="pb-2 font-medium">Created</th>
              </tr>
            </thead>
            <tbody>
              {jobs.data.slice(0, 8).map((j) => (
                <tr key={j.id} className="border-t border-slate-100">
                  <td className="py-2 font-mono text-xs text-slate-500">
                    {j.id.slice(0, 8)}
                  </td>
                  <td className="py-2 text-slate-600">{j.stage ?? "—"}</td>
                  <td className="py-2">
                    <StatusBadge status={j.status} />
                  </td>
                  <td className="py-2 text-xs text-slate-400">
                    {j.created_at ? new Date(j.created_at).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
