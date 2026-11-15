"use client";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Server } from "lucide-react";
import { Callout, EmptyState, ErrorState, HealthIndicator, Icon, PageHeader, Skeleton, StatusBadge, TBody, Table, TableSkeleton, Td, Th, THead, Tr } from "@/components/ui";
import { systemApi } from "@/lib/api/resources";
import type { SecurityStatus } from "@/lib/api/types";
import { titleCase } from "../knowledge-bases/ingestion";

const LANGFUSE_URL = process.env.NEXT_PUBLIC_LANGFUSE_URL || "http://localhost:3000";

type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

interface Posture {
  label: string;
  value: string;
  tone: Tone;
  note: string;
}

const AUTH_MODES: Record<SecurityStatus["auth_mode"], string> = {
  disabled: "Off",
  jwt: "Sign-in with an identity provider (JWT)",
  trusted_header: "Identity from a trusted proxy header",
};

function posture(s: SecurityStatus): Posture[] {
  const open = s.auth_mode === "disabled";
  return [
    {
      label: "Authentication",
      value: AUTH_MODES[s.auth_mode] ?? s.auth_mode,
      tone: open ? "danger" : "ok",
      note: open ? "Anyone who can reach the API can use every admin route and read every Knowledge Base." : "Callers must present an identity. Roles and Grants decide what they can do.",
    },
    {
      label: "Application API keys",
      value: s.api_keys_enforced ? "Required" : "Not required",
      tone: s.api_keys_enforced ? "ok" : "warn",
      note: s.api_keys_enforced ? "The OpenAI-compatible endpoint needs a valid Application key on every request." : open ? "The OpenAI-compatible endpoint accepts any request, with or without a key." : "Callers need either a valid Application key or their own identity.",
    },
    {
      label: "Knowledge Bases with no Grants",
      value: s.ungranted_kb_access === "deny" ? "Unreadable until granted" : "Everyone can read them",
      tone: open ? "neutral" : s.ungranted_kb_access === "deny" ? "ok" : "warn",
      note: open
        ? "Moot while authentication is off: everyone can read everything."
        : s.ungranted_kb_access === "deny"
          ? "Signed-in callers cannot read a Knowledge Base until someone is granted it."
          : "A Knowledge Base nobody has been granted is open to every signed-in caller.",
    },
    {
      label: "Admin retrieval",
      value: s.admin_bypass_retrieval ? "Admins read every Knowledge Base" : "Admins need a Grant like anyone else",
      tone: s.admin_bypass_retrieval ? "warn" : "ok",
      note: s.admin_bypass_retrieval ? "Being an Admin is enough to retrieve from any Knowledge Base, whatever its Grants." : "Administering the platform does not by itself let someone read Documents.",
    },
    ...(s.user_token_header
      ? [{ label: "Forwarded end user", value: s.user_token_header, tone: "neutral" as const, note: "A host application may send its end user's token in this header so Grants apply to that person." }]
      : []),
  ];
}

export default function SystemPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: systemApi.health, refetchInterval: 10_000 });
  const security = useQuery({ queryKey: ["system", "security"], queryFn: systemApi.security });
  const gpu = useQuery({ queryKey: ["gpu"], queryFn: systemApi.gpu, refetchInterval: 10_000 });

  return (
    <div className="space-y-8">
      <PageHeader title="System" description="Whether each part of the deployment is up, how access is locked down, and where to look when something is slow." />

      <section aria-labelledby="health-title" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="health-title" className="font-display text-lg font-semibold">
            Dependencies
          </h2>
          {health.data && <StatusBadge status={titleCase(health.data.status)} />}
        </div>
        {health.isLoading && <TableSkeleton columns={4} rows={4} label="Checking dependencies" />}
        {health.isError && <ErrorState title="Could not check the dependencies" message={(health.error as Error).message} onRetry={() => health.refetch()} />}
        {health.data && health.data.components.length === 0 && <EmptyState icon={Server} title="No dependencies reported" description="The API answered but listed nothing to check." />}
        {health.data && health.data.components.length > 0 && (
          <Table caption="Dependency health">
            <THead>
              <tr>
                <Th>Dependency</Th>
                <Th>Status</Th>
                <Th>Version</Th>
                <Th align="right">Response</Th>
              </tr>
            </THead>
            <TBody>
              {health.data.components.map((c) => (
                <Tr key={c.name}>
                  <Td>
                    <div className="flex items-center gap-2 font-medium">
                      <HealthIndicator status={c.status} />
                      {c.name}
                    </div>
                    {c.detail && <p className="mt-0.5 max-w-md text-xs text-muted [overflow-wrap:anywhere]">{c.detail}</p>}
                  </Td>
                  <Td>
                    <StatusBadge status={titleCase(c.status)} />
                  </Td>
                  <Td mono>{c.version ?? "—"}</Td>
                  <Td align="right">{c.latency_ms != null ? `${c.latency_ms} ms` : "—"}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        )}
      </section>

      <section aria-labelledby="security-title" className="space-y-3">
        <h2 id="security-title" className="font-display text-lg font-semibold">
          Security posture
        </h2>
        {security.isLoading && <Skeleton className="h-40 w-full" />}
        {security.isError && <ErrorState title="Could not read the security posture" message={(security.error as Error).message} onRetry={() => security.refetch()} />}
        {security.data && (
          <>
            {security.data.auth_mode === "disabled" && (
              <Callout tone="danger" title="Authentication is off">
                This is the shipped default for trying the platform out. Turn on authentication (<code>AUTH__MODE</code>) before real Documents go in.
              </Callout>
            )}
            <dl className="card divide-y divide-line">
              {posture(security.data).map((p) => (
                <div key={p.label} className="grid grid-cols-1 gap-1 px-5 py-3.5 text-sm sm:grid-cols-[14rem_1fr] sm:gap-4">
                  <dt className="font-medium text-fg">{p.label}</dt>
                  <dd>
                    <StatusBadge status={p.value} tone={p.tone} />
                    <p className="mt-1.5 text-muted">{p.note}</p>
                  </dd>
                </div>
              ))}
            </dl>
          </>
        )}
      </section>

      <section aria-labelledby="gpu-title" className="space-y-3">
        <h2 id="gpu-title" className="font-display text-lg font-semibold">
          GPU
        </h2>
        {gpu.isLoading && <Skeleton className="h-20 w-full" />}
        {gpu.isError && <ErrorState title="Could not read GPU use" message={(gpu.error as Error).message} onRetry={() => gpu.refetch()} />}
        {gpu.data && !gpu.data.available && <div className="card p-5 text-sm text-muted">{gpu.data.message || "No GPU detected. Models run on the CPU."}</div>}
        {gpu.data?.available && (
          <>
            <p className="text-xs text-muted">
              CUDA {gpu.data.cuda_version ?? "?"} · Driver {gpu.data.driver_version ?? "?"}
            </p>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {gpu.data.gpus.map((g) => (
                <div key={g.index} className="card p-5">
                  <div className="text-sm font-semibold">
                    GPU {g.index} · {g.name}
                  </div>
                  <dl className="mt-3 grid grid-cols-3 gap-3 text-center">
                    <div className="flex flex-col-reverse">
                      <dt className="text-xs text-muted">In use</dt>
                      <dd className="text-lg font-semibold tabular-nums">{g.utilization_percent ?? "—"}%</dd>
                    </div>
                    <div className="flex flex-col-reverse">
                      <dt className="text-xs text-muted">Memory (MB)</dt>
                      <dd className="text-lg font-semibold tabular-nums">
                        {g.memory_used_mb ?? "—"}/{g.memory_total_mb ?? "—"}
                      </dd>
                    </div>
                    <div className="flex flex-col-reverse">
                      <dt className="text-xs text-muted">Temperature</dt>
                      <dd className="text-lg font-semibold tabular-nums">{g.temperature_c ?? "—"}°</dd>
                    </div>
                  </dl>
                </div>
              ))}
            </div>
          </>
        )}
      </section>

      <section aria-labelledby="observability-title" className="space-y-3">
        <h2 id="observability-title" className="font-display text-lg font-semibold">
          Observability
        </h2>
        <a href={LANGFUSE_URL} target="_blank" rel="noreferrer" className="card flex items-center justify-between gap-3 p-5 hover:border-accent">
          <span>
            <span className="block text-sm font-semibold text-accent-text">Open Langfuse</span>
            <span className="mt-1 block text-sm text-muted">Traces of each answer, model calls and timings.</span>
          </span>
          <Icon icon={ExternalLink} className="text-subtle" />
        </a>
      </section>
    </div>
  );
}
