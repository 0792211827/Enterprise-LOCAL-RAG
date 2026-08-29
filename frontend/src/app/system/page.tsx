"use client";
import { useQuery } from "@tanstack/react-query";
import {
  ErrorState,
  HealthIndicator,
  LoadingState,
  MetricCard,
  PageHeader,
  StatusBadge,
} from "@/components/ui";
import { systemApi } from "@/lib/api/resources";

const LANGFUSE_URL = process.env.NEXT_PUBLIC_LANGFUSE_URL || "http://localhost:3000";

export default function SystemPage() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: systemApi.health,
    refetchInterval: 10_000,
  });
  const gpu = useQuery({ queryKey: ["gpu"], queryFn: systemApi.gpu, refetchInterval: 10_000 });

  return (
    <div>
      <PageHeader
        title="System"
        description="Infrastructure health, GPU telemetry and observability links."
      />

      <section className="card mb-6">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <span className="text-sm font-semibold text-slate-700">Component Health</span>
          {health.data && <StatusBadge status={health.data.status} />}
        </div>
        {health.isLoading && <LoadingState />}
        {health.isError && <ErrorState message={(health.error as Error).message} />}
        {health.data && (
          <table className="w-full text-sm">
            <tbody>
              {health.data.components.map((c) => (
                <tr key={c.name} className="border-t border-slate-100">
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2 font-medium text-slate-700">
                      <HealthIndicator status={c.status} />
                      {c.name}
                    </div>
                    {c.detail && <div className="text-xs text-slate-400">{c.detail}</div>}
                  </td>
                  <td className="px-4 py-3 text-right text-xs text-slate-500">
                    {c.version && <div>v{c.version}</div>}
                    {c.latency_ms != null && <div>{c.latency_ms}ms</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="mb-6">
        <h2 className="mb-3 text-sm font-semibold text-slate-700">GPU</h2>
        {gpu.isLoading && <LoadingState />}
        {gpu.data && !gpu.data.available && (
          <div className="card p-5 text-sm text-slate-500">
            {gpu.data.message || "No GPU detected. Running on CPU."}
          </div>
        )}
        {gpu.data?.available && (
          <>
            <p className="mb-3 text-xs text-slate-400">
              CUDA {gpu.data.cuda_version ?? "?"} · Driver {gpu.data.driver_version ?? "?"}
            </p>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {gpu.data.gpus.map((g) => (
                <div key={g.index} className="card p-5">
                  <div className="text-sm font-semibold text-slate-700">
                    GPU {g.index} · {g.name}
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-3 text-center">
                    <div>
                      <div className="text-lg font-semibold text-slate-900">
                        {g.utilization_percent ?? "—"}%
                      </div>
                      <div className="text-xs text-slate-400">Util</div>
                    </div>
                    <div>
                      <div className="text-lg font-semibold text-slate-900">
                        {g.memory_used_mb ?? "—"}/{g.memory_total_mb ?? "—"}
                      </div>
                      <div className="text-xs text-slate-400">MB</div>
                    </div>
                    <div>
                      <div className="text-lg font-semibold text-slate-900">
                        {g.temperature_c ?? "—"}°
                      </div>
                      <div className="text-xs text-slate-400">Temp</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold text-slate-700">Observability</h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <a href={LANGFUSE_URL} target="_blank" rel="noreferrer" className="card block p-5 hover:ring-2 hover:ring-brand-200">
            <div className="text-sm font-semibold text-brand-700">Langfuse →</div>
            <p className="mt-1 text-xs text-slate-400">
              LLM traces, evaluations and prompt analytics.
            </p>
          </a>
        </div>
      </section>
    </div>
  );
}
