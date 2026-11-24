"use client";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Download, ScrollText, SearchX } from "lucide-react";
import { FormEvent, ReactNode, useState } from "react";
import {
  Advanced,
  Callout,
  Drawer,
  EmptyState,
  ErrorState,
  Icon,
  PageHeader,
  StatusBadge,
  TBody,
  Table,
  TableSkeleton,
  Td,
  Th,
  THead,
  Tr,
  useToast,
} from "@/components/ui";
import { auditApi } from "@/lib/api/resources";
import type { AuditAction, AuditEvent, AuditFilters, AuditOutcome, AuditStatus } from "@/lib/api/types";
import { saveBlob } from "@/lib/download";
import { formatDateTime } from "@/lib/format";

const ACTIONS: { value: AuditAction; label: string }[] = [
  { value: "query.answer", label: "Answer" },
  { value: "query.search", label: "Search" },
  { value: "admin.mutation", label: "Admin change" },
  { value: "access.denied", label: "Access denied" },
  { value: "audit.export", label: "Audit export" },
  { value: "audit.purge", label: "Audit purge" },
  { value: "uploads.cleanup", label: "Upload cleanup" },
  { value: "chat.purge", label: "Chat purge" },
  { value: "connector.sync", label: "Connector sync" },
];
const ACTION_LABELS: Record<string, string> = Object.fromEntries(ACTIONS.map((a) => [a.value, a.label]));
const OUTCOMES: AuditOutcome[] = ["ok", "denied", "error"];

/** <input type="datetime-local"> is local time; the API wants an instant. */
function toInstant(local: string): string | undefined {
  return local ? new Date(local).toISOString() : undefined;
}

const short = (id: string | null | undefined) => (id ? id.slice(0, 8) : "—");

const none = <span className="text-subtle">—</span>;

function kbList(ids: string[] | null, nullMeans: string) {
  if (ids === null) return <span className="text-muted">{nullMeans}</span>;
  if (ids.length === 0) return <span className="text-muted">none</span>;
  return <span className="font-mono text-xs">{ids.join(", ")}</span>;
}

function Target({ event: e }: { event: AuditEvent }) {
  if (e.action === "admin.mutation" || e.action === "access.denied") {
    return (
      <span className="font-mono text-xs">
        {e.http_method} {e.route}
      </span>
    );
  }
  return <span className="font-mono text-xs text-muted">{e.route ?? "—"}</span>;
}

function RecordingStatus({ status }: { status: AuditStatus }) {
  if (status.level === "off") {
    return (
      <Callout tone="warn" title="Audit logging is off">
        Nothing is being recorded. Set <code>AUDIT__LEVEL=metadata</code> (IDs and digests) or <code>AUDIT__LEVEL=full</code> (also the query and answer text) on the API and restart it. Events
        recorded earlier are still listed below.
      </Callout>
    );
  }
  return (
    <p className="text-sm text-muted">
      Recording at level <strong className="font-medium text-fg">{status.level}</strong>
      {status.level === "metadata" && " (IDs and SHA-256 digests, no query or answer text)"} · when a write fails:{" "}
      <strong className="font-medium text-fg">{status.fail_mode === "deny" ? "refuse the request" : "log and continue"}</strong> · retention:{" "}
      <strong className="font-medium text-fg">{status.retention_days ? `${status.retention_days} days` : "forever"}</strong>
    </p>
  );
}

const EMPTY_FORM = {
  actor: "",
  action: "" as AuditAction | "",
  outcome: "" as AuditOutcome | "",
  knowledge_base_id: "",
  application_id: "",
  request_id: "",
  since: "",
  until: "",
};

function FilterBar({ onApply }: { onApply: (filters: AuditFilters) => void }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const set = (key: keyof typeof EMPTY_FORM) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    onApply({
      actor: form.actor.trim() || undefined,
      action: form.action || undefined,
      outcome: form.outcome || undefined,
      knowledge_base_id: form.knowledge_base_id.trim() || undefined,
      application_id: form.application_id.trim() || undefined,
      request_id: form.request_id.trim() || undefined,
      since: toInstant(form.since),
      until: toInstant(form.until),
    });
  };

  return (
    <form onSubmit={submit} aria-label="Filter the Audit Log" className="card mb-4 space-y-3 p-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <div>
          <label htmlFor="audit-actor" className="label">
            Actor
          </label>
          <input id="audit-actor" className="input" value={form.actor} onChange={set("actor")} placeholder="Exact subject" />
        </div>
        <div>
          <label htmlFor="audit-action" className="label">
            Action
          </label>
          <select id="audit-action" className="input" value={form.action} onChange={set("action")}>
            <option value="">Any</option>
            {ACTIONS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="audit-outcome" className="label">
            Outcome
          </label>
          <select id="audit-outcome" className="input" value={form.outcome} onChange={set("outcome")}>
            <option value="">Any</option>
            {OUTCOMES.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="audit-since" className="label">
            From
          </label>
          <input id="audit-since" className="input" type="datetime-local" value={form.since} onChange={set("since")} />
        </div>
        <div>
          <label htmlFor="audit-until" className="label">
            Until
          </label>
          <input id="audit-until" className="input" type="datetime-local" value={form.until} onChange={set("until")} />
        </div>
      </div>
      <Advanced title="More filters">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div>
            <label htmlFor="audit-request" className="label">
              Request ID
            </label>
            <input id="audit-request" className="input font-mono text-xs" value={form.request_id} onChange={set("request_id")} />
          </div>
          <div>
            <label htmlFor="audit-kb" className="label">
              Knowledge Base ID
            </label>
            <input id="audit-kb" className="input font-mono text-xs" value={form.knowledge_base_id} onChange={set("knowledge_base_id")} />
          </div>
          <div>
            <label htmlFor="audit-app" className="label">
              Application ID
            </label>
            <input id="audit-app" className="input font-mono text-xs" value={form.application_id} onChange={set("application_id")} />
          </div>
        </div>
      </Advanced>
      <div className="flex justify-end gap-2">
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => {
            setForm(EMPTY_FORM);
            onApply({});
          }}
        >
          Clear
        </button>
        <button type="submit" className="btn btn-primary">
          Apply filters
        </button>
      </div>
    </form>
  );
}

function Field({ label, children }: { label: string; children?: ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-0.5 py-2 text-sm sm:grid-cols-[9rem_1fr] sm:gap-3">
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 text-fg [overflow-wrap:anywhere]">{children ?? none}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      <dl className="divide-y divide-line">{children}</dl>
    </section>
  );
}

const mono = (value: string | null | undefined) => (value ? <span className="font-mono text-xs">{value}</span> : none);

function EventDetail({ event }: { event: AuditEvent }) {
  const isQuery = event.action.startsWith("query.");
  return (
    <div className="space-y-6">
      <Section title="Event">
        <Field label="When">{formatDateTime(event.occurred_at)}</Field>
        <Field label="Action">{ACTION_LABELS[event.action] ?? event.action}</Field>
        <Field label="Outcome">
          <StatusBadge status={event.outcome} />
          {event.status_code !== null && <span className="ml-2 text-muted">HTTP {event.status_code}</span>}
          {event.error_code && <span className="ml-2 font-mono text-xs text-danger">{event.error_code}</span>}
        </Field>
        <Field label="Request ID">{mono(event.request_id)}</Field>
        <Field label="Route">
          <Target event={event} />
        </Field>
        {event.resource_id && (
          <Field label="Resource">
            {event.resource_type} {mono(event.resource_id)}
          </Field>
        )}
        {event.latency_ms !== null && <Field label="Latency">{event.latency_ms} ms</Field>}
      </Section>

      <Section title="Who">
        <Field label="Subject">{event.actor_subject ?? <span className="text-muted">not authenticated</span>}</Field>
        <Field label="Signed in with">{event.auth_method}</Field>
        <Field label="Roles">{event.actor_roles?.join(", ") || none}</Field>
        <Field label="Groups">{event.actor_groups?.join(", ") || none}</Field>
        {event.api_key_id && <Field label="API key">{mono(event.api_key_id)}</Field>}
        {event.application_id && <Field label="Application">{mono(event.application_id)}</Field>}
        <Field label="Source IP">{event.source_ip}</Field>
        <Field label="User agent">{event.user_agent}</Field>
      </Section>

      {isQuery && (
        <Section title="Knowledge scope">
          <Field label="Requested">{kbList(event.requested_kb_ids, "every Knowledge Base (global search)")}</Field>
          <Field label="Permitted">{kbList(event.permitted_kb_ids, "unrestricted")}</Field>
          <Field label="Dropped by Grants">{kbList(event.dropped_kb_ids, "none")}</Field>
          {event.cache_hit !== null && <Field label="Cache hit">{event.cache_hit ? "yes" : "no"}</Field>}
          {event.model && <Field label="Model">{event.model}</Field>}
        </Section>
      )}

      {isQuery && (
        <Section title="Retrieved">
          {event.retrieved === null ? (
            <p className="py-2 text-sm text-muted">No retrieval ran for this request.</p>
          ) : event.retrieved.length === 0 ? (
            <p className="py-2 text-sm text-muted">Nothing was retrieved.</p>
          ) : (
            <div className="overflow-x-auto py-2">
              <table className="w-full text-xs">
                <caption className="sr-only">Retrieved passages</caption>
                <thead className="text-left text-muted">
                  <tr>
                    <th scope="col" className="py-1 pr-3 font-medium">
                      Chunk
                    </th>
                    <th scope="col" className="py-1 pr-3 font-medium">
                      Document
                    </th>
                    <th scope="col" className="py-1 pr-3 font-medium">
                      Knowledge Base
                    </th>
                    <th scope="col" className="py-1 text-right font-medium">
                      Score
                    </th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {event.retrieved.map((r, i) => (
                    <tr key={`${r.chunk_id}-${i}`} className="border-t border-line">
                      <td className="py-1 pr-3" title={r.chunk_id ?? ""}>
                        {short(r.chunk_id)}
                      </td>
                      <td className="py-1 pr-3" title={r.document_id ?? ""}>
                        {short(r.document_id)}
                      </td>
                      <td className="py-1 pr-3" title={r.knowledge_base_id ?? ""}>
                        {short(r.knowledge_base_id)}
                      </td>
                      <td className="py-1 text-right">{r.score?.toFixed(3) ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}

      {isQuery && (
        <Section title="Exchange">
          <Field label="Query">{event.query_text ?? <span className="text-muted">Not stored (level metadata)</span>}</Field>
          <Field label="Query SHA-256">{mono(event.query_sha256)}</Field>
          {event.action === "query.answer" && (
            <>
              <Field label="Answer">{event.answer_text ?? <span className="text-muted">Not stored (level metadata)</span>}</Field>
              <Field label="Answer SHA-256">{mono(event.answer_sha256)}</Field>
            </>
          )}
        </Section>
      )}

      {event.details && (
        <section>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Details</h3>
          <pre className="overflow-x-auto rounded-control bg-sunken p-3 text-xs text-fg">{JSON.stringify(event.details, null, 2)}</pre>
        </section>
      )}
    </div>
  );
}

function ExportButtons({ filters }: { filters: AuditFilters }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);

  const download = async (format: "ndjson" | "csv") => {
    setBusy(format);
    try {
      const { blob, filename } = await auditApi.export(filters, format);
      saveBlob(blob, filename ?? `audit.${format}`);
      toast({ title: `Audit Log exported as ${format.toUpperCase()}`, description: filename ?? undefined });
    } catch (err) {
      toast({ title: "Could not export the Audit Log", description: (err as Error).message, tone: "danger" });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col items-start gap-1 sm:items-end">
      <div className="flex gap-2">
        <button type="button" className="btn btn-secondary whitespace-nowrap" disabled={busy !== null} onClick={() => download("csv")}>
          <Icon icon={Download} size="sm" />
          {busy === "csv" ? "Exporting…" : "Export CSV"}
        </button>
        <button type="button" className="btn btn-secondary whitespace-nowrap" disabled={busy !== null} onClick={() => download("ndjson")}>
          {busy === "ndjson" ? "Exporting…" : "Export NDJSON"}
        </button>
      </div>
      <p className="text-xs text-muted">Exports use the filters applied below and are themselves audited.</p>
    </div>
  );
}

export default function AuditPage() {
  const [filters, setFilters] = useState<AuditFilters>({});
  const [selected, setSelected] = useState<AuditEvent | null>(null);

  const status = useQuery({ queryKey: ["audit", "status"], queryFn: auditApi.status });
  const events = useInfiniteQuery({
    queryKey: ["audit", "events", filters],
    queryFn: ({ pageParam }) => auditApi.events(filters, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const rows = events.data?.pages.flatMap((page) => page.items) ?? [];
  const filtered = Object.values(filters).some(Boolean);

  return (
    <div>
      <PageHeader
        title="Audit Log"
        description="Who asked what, which Knowledge Bases they were allowed to search, what was retrieved and answered, and every change to the control plane."
        action={<ExportButtons filters={filters} />}
      />
      <div className="mb-4">
        {status.data && <RecordingStatus status={status.data} />}
        {status.isError && <ErrorState title="Could not read the recording status" message={(status.error as Error).message} onRetry={() => status.refetch()} />}
      </div>

      <FilterBar onApply={setFilters} />

      {events.isLoading && <TableSkeleton columns={6} rows={6} label="Loading the Audit Log" />}
      {events.isError && <ErrorState title="Could not load the Audit Log" message={(events.error as Error).message} onRetry={() => events.refetch()} />}
      {events.data && rows.length === 0 && (
        <EmptyState
          icon={filtered ? SearchX : ScrollText}
          title={filtered ? "No events match these filters" : "No audit events yet"}
          description={filtered ? "Widen the time range or clear a filter." : "Events appear here as people ask questions and Admins change things."}
        />
      )}
      {rows.length > 0 && (
        <div className="space-y-3">
          <Table caption="Audit events, newest first">
            <THead>
              <tr>
                <Th>When</Th>
                <Th>Actor</Th>
                <Th>Action</Th>
                <Th>Target</Th>
                <Th>Outcome</Th>
                <Th align="right">Retrieved</Th>
              </tr>
            </THead>
            <TBody>
              {rows.map((e) => (
                <Tr key={e.id} onClick={() => setSelected(e)}>
                  <Td>
                    <button type="button" className="whitespace-nowrap rounded-sm text-left text-xs underline-offset-2 hover:underline" aria-label={`Open event from ${formatDateTime(e.occurred_at)}`}>
                      {formatDateTime(e.occurred_at)}
                    </button>
                  </Td>
                  <Td>
                    <div className="max-w-[14rem] truncate" title={e.actor_subject ?? ""}>
                      {e.actor_subject ?? <span className="text-muted">anonymous</span>}
                    </div>
                    {e.auth_method && <div className="text-xs text-muted">{e.auth_method}</div>}
                  </Td>
                  <Td>{ACTION_LABELS[e.action] ?? e.action}</Td>
                  <Td>
                    <Target event={e} />
                  </Td>
                  <Td>
                    <StatusBadge status={e.outcome} />
                  </Td>
                  <Td align="right">{e.cache_hit ? "cached" : e.retrieved === null ? "—" : e.retrieved.length}</Td>
                </Tr>
              ))}
            </TBody>
          </Table>
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted">
            <span>
              Showing {rows.length} event{rows.length === 1 ? "" : "s"}
              {events.hasNextPage ? ", newest first" : ""}
            </span>
            {events.hasNextPage && (
              <button type="button" className="btn btn-secondary" disabled={events.isFetchingNextPage} onClick={() => events.fetchNextPage()}>
                {events.isFetchingNextPage ? "Loading…" : "Load older events"}
              </button>
            )}
          </div>
          {events.isFetchNextPageError && <ErrorState title="Could not load older events" message={(events.error as Error).message} onRetry={() => events.fetchNextPage()} />}
        </div>
      )}

      <Drawer open={selected !== null} onClose={() => setSelected(null)} title="Audit event" size="lg">
        {selected && <EventDetail event={selected} />}
      </Drawer>
    </div>
  );
}
