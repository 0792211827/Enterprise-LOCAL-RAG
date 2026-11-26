"use client";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CheckCircle2, OctagonAlert, TriangleAlert } from "lucide-react";
import Link from "next/link";
import {
  EmptyState,
  ErrorState,
  HealthIndicator,
  Icon,
  MetricCard,
  PageHeader,
  Skeleton,
  StatusBadge,
  TBody,
  Table,
  TableSkeleton,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui";
import { applicationsApi, connectorsApi, documentsApi, systemApi } from "@/lib/api/resources";
import { attentionItems } from "@/lib/attention";
import { timeAgo } from "@/lib/format";
import { titleCase } from "./knowledge-bases/ingestion";

const REFRESH = 15_000;
// The API returns 100 rows unless asked for more, and a failure past the hundredth must not be missed.
const DASHBOARD_LIMIT = 1000;

export default function DashboardPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: systemApi.health, refetchInterval: REFRESH });
  const stats = useQuery({ queryKey: ["stats"], queryFn: systemApi.stats, refetchInterval: REFRESH });
  const gpu = useQuery({ queryKey: ["gpu"], queryFn: systemApi.gpu });
  const jobs = useQuery({ queryKey: ["dashboard", "jobs"], queryFn: () => documentsApi.jobsUpTo(DASHBOARD_LIMIT), refetchInterval: REFRESH });
  const connectors = useQuery({ queryKey: ["connectors"], queryFn: connectorsApi.list, refetchInterval: REFRESH });
  const applications = useQuery({ queryKey: ["dashboard", "applications"], queryFn: () => applicationsApi.listUpTo(DASHBOARD_LIMIT), refetchInterval: REFRESH });

  const checks = [
    { name: "service health", q: health },
    { name: "ingestion jobs", q: jobs },
    { name: "Connectors", q: connectors },
    { name: "Applications", q: applications },
  ];
  const loading = checks.some((c) => c.q.isLoading);
  const unavailable = checks.filter((c) => c.q.isError).map((c) => c.name);

  const items = attentionItems({
    services: health.data?.components,
    jobs: jobs.data,
    connectors: connectors.data,
    applications: applications.data,
  });

  return (
    <div className="space-y-8">
      <PageHeader title="Dashboard" description="Whether everything is working, and what to fix first." />

      <OverallStatus loading={loading} count={items.length} danger={items.some((i) => i.tone === "danger")} unavailable={unavailable} />

      <section aria-labelledby="attention-title" className="space-y-3">
        <h2 id="attention-title" className="font-display text-lg font-semibold">
          Needs action
        </h2>
        {loading && items.length === 0 && <Skeleton className="h-16 w-full" />}
        {items.length > 0 && (
          <ul aria-label="Needs action" className="card divide-y divide-line">
            {items.map((item) => (
              <li key={item.id}>
                <Link href={item.href} className="flex items-center gap-3 px-5 py-3.5 hover:bg-sunken/50">
                  <Icon icon={item.tone === "danger" ? OctagonAlert : TriangleAlert} className={item.tone === "danger" ? "text-danger" : "text-clay-text"} />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium text-fg">{item.title}</span>
                    {item.detail && <span className="block text-sm text-muted [overflow-wrap:anywhere]">{item.detail}</span>}
                  </span>
                  <span className="flex shrink-0 items-center gap-1.5 text-sm font-medium text-accent-text">
                    {item.action}
                    <Icon icon={ArrowRight} size="sm" />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
        {!loading && items.length === 0 && unavailable.length === 0 && (
          <div className="flex items-center gap-3 rounded-panel border border-ok/30 bg-ok-soft px-5 py-4 text-sm">
            <Icon icon={CheckCircle2} className="text-ok" />
            <span className="text-fg">Nothing needs action.</span>
          </div>
        )}
        {!loading && items.length === 0 && unavailable.length > 0 && <p className="text-sm text-muted">Nothing else is flagged, but some checks could not run (see above).</p>}
      </section>

      <section aria-labelledby="counts-title" className="space-y-3">
        <h2 id="counts-title" className="font-display text-lg font-semibold">
          At a glance
        </h2>
        {stats.isLoading && <Skeleton className="h-24 w-full" />}
        {stats.isError && <ErrorState title="Could not load the counts" message={(stats.error as Error).message} onRetry={() => stats.refetch()} />}
        {stats.data && (
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <MetricCard label="Knowledge Bases" value={stats.data.knowledge_bases} />
            <MetricCard label="Documents" value={stats.data.documents} />
            <MetricCard label="Applications" value={stats.data.applications} />
            <MetricCard label="Inference endpoints" value={stats.data.providers} />
          </div>
        )}
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section aria-labelledby="services-title" className="card space-y-3 p-5">
          <div className="flex items-center justify-between gap-2">
            <h2 id="services-title" className="font-display text-lg font-semibold">
              Services
            </h2>
            <Link href="/system" className="text-sm font-medium text-accent-text">
              System details
            </Link>
          </div>
          {health.isLoading && <Skeleton className="h-24 w-full" />}
          {health.isError && <p className="text-sm text-danger">Could not check the services: {(health.error as Error).message}</p>}
          {health.data && (
            <ul className="space-y-2.5">
              {health.data.components.map((c) => (
                <li key={c.name} className="flex items-center justify-between gap-3 text-sm">
                  <span className="flex items-center gap-2.5">
                    <HealthIndicator status={c.status} />
                    {c.name}
                  </span>
                  <span className="flex items-center gap-3 text-xs text-muted">
                    {c.latency_ms != null && <span>{c.latency_ms} ms</span>}
                    <StatusBadge status={titleCase(c.status)} />
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="gpu-title" className="card space-y-3 p-5">
          <h2 id="gpu-title" className="font-display text-lg font-semibold">
            GPU
          </h2>
          {gpu.isLoading && <Skeleton className="h-24 w-full" />}
          {gpu.isError && <p className="text-sm text-danger">Could not read GPU use: {(gpu.error as Error).message}</p>}
          {gpu.data && !gpu.data.available && <p className="text-sm text-muted">{gpu.data.message || "No GPU detected. Models run on the CPU."}</p>}
          {gpu.data?.available &&
            gpu.data.gpus.map((g) => (
              <div key={g.index} className="text-sm">
                <div className="font-medium">{g.name}</div>
                <div className="mt-0.5 text-xs text-muted">
                  {g.memory_used_mb ?? "?"} / {g.memory_total_mb ?? "?"} MB · {g.utilization_percent ?? "?"}% in use · {g.temperature_c ?? "?"}°C
                </div>
              </div>
            ))}
        </section>
      </div>

      <section aria-labelledby="jobs-title" className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 id="jobs-title" className="font-display text-lg font-semibold">
            Recent ingestion
          </h2>
          <Link href="/documents" className="text-sm font-medium text-accent-text">
            All Documents
          </Link>
        </div>
        {jobs.isLoading && <TableSkeleton columns={4} rows={3} label="Loading ingestion jobs" />}
        {jobs.isError && <ErrorState title="Could not load ingestion jobs" message={(jobs.error as Error).message} onRetry={() => jobs.refetch()} />}
        {jobs.data && jobs.data.length === 0 && <EmptyState title="No ingestion yet" description="Upload a Document to a Knowledge Base and its progress shows here." />}
        {jobs.data && jobs.data.length > 0 && (
          <Table caption="Recent ingestion jobs">
            <THead>
              <tr>
                <Th>Job</Th>
                <Th>Stage</Th>
                <Th>Status</Th>
                <Th>Started</Th>
              </tr>
            </THead>
            <TBody>
              {[...jobs.data]
                .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))
                .slice(0, 8)
                .map((j) => (
                  <Tr key={j.id}>
                    <Td mono>
                      <Link href={`/knowledge-bases/${j.knowledge_base_id}`} className="text-accent-text underline">
                        {j.id.slice(0, 8)}
                      </Link>
                    </Td>
                    <Td>{j.stage ? titleCase(j.stage) : "—"}</Td>
                    <Td>
                      <StatusBadge status={titleCase(j.status)} />
                    </Td>
                    <Td>{timeAgo(j.created_at)}</Td>
                  </Tr>
                ))}
            </TBody>
          </Table>
        )}
      </section>
    </div>
  );
}

function OverallStatus({ loading, count, danger, unavailable }: { loading: boolean; count: number; danger: boolean; unavailable: string[] }) {
  if (loading && count === 0) {
    return (
      <div role="status" className="card p-5">
        <span className="sr-only">Checking the system</span>
        <Skeleton className="h-8 w-1/3" />
      </div>
    );
  }
  const partial = unavailable.length > 0;
  const tone = danger ? "danger" : count > 0 || partial ? "warn" : "ok";
  const headline = count > 0 ? (count === 1 ? "1 thing needs action" : `${count} things need action`) : partial ? "Some checks could not run" : "Everything is OK";
  const note = partial
    ? `Could not check ${unavailable.join(", ")}.`
    : count > 0
      ? "Fix these first, starting at the top."
      : "Services are up and nothing is waiting on you.";
  const styles = {
    ok: { box: "border-ok/30 bg-ok-soft", icon: CheckCircle2, color: "text-ok" },
    warn: { box: "border-clay/30 bg-clay-soft", icon: TriangleAlert, color: "text-clay-text" },
    danger: { box: "border-danger/30 bg-danger-soft", icon: OctagonAlert, color: "text-danger" },
  }[tone];

  return (
    <div role="status" aria-label="Overall status" className={`flex items-center gap-4 rounded-panel border p-5 ${styles.box}`}>
      <Icon icon={styles.icon} size="xl" className={styles.color} />
      <div>
        <div className="font-display text-xl font-semibold text-fg">{headline}</div>
        <p className="mt-0.5 text-sm text-muted">{note}</p>
      </div>
    </div>
  );
}
