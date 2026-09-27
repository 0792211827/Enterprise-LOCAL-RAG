"use client";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { FormEvent, ReactNode, useState } from "react";
import {
  Callout,
  EmptyState,
  ErrorState,
  LoadingState,
  Modal,
  PageHeader,
  StatusBadge,
} from "@/components/ui";
import { auditApi } from "@/lib/api/resources";
import type { AuditAction, AuditEvent, AuditFilters, AuditOutcome, AuditStatus } from "@/lib/api/types";

const ACTIONS: { value: AuditAction; label: string }[] = [
  { value: "query.answer", label: "Answer" },
  { value: "query.search", label: "Search" },
  { value: "admin.mutation", label: "Admin change" },
  { value: "access.denied", label: "Access denied" },
  { value: "audit.export", label: "Audit export" },
  { value: "audit.purge", label: "Audit purge" },
];
const ACTION_LABELS = Object.fromEntries(ACTIONS.map((a) => [a.value, a.label]));
const OUTCOMES: AuditOutcome[] = ["ok", "denied", "error"];

function fmt(ts: string) {
  return new Date(ts).toLocaleString();
}

/** <input type="datetime-local"> is local time; the API wants an instant. */
function toInstant(local: string): string | undefined {
  return local ? new Date(local).toISOString() : undefined;
}

function short(id: string | null | undefined) {
  return id ? id.slice(0, 8) : "—";
}

function kbList(ids: string[] | null, nullMeans: string) {
  if (ids === null) return <span className="text-slate-400">{nullMeans}</span>;
  if (ids.length === 0) return <span className="text-slate-400">none</span>;
  return <span className="font-mono text-xs">{ids.join(", ")}</span>;
}

function target(e: AuditEvent) {
  if (e.action === "admin.mutation" || e.action === "access.denied") {
    return (
      <span className="font-mono text-xs">
        {e.http_method} {e.route}
      </span>
    );
  }
  return <span className="font-mono text-xs text-slate-500">{e.route ?? "—"}</span>;
}

function RecordingStatus({ status }: { status: AuditStatus }) {
  if (status.level === "off") {
    return (
      <Callout tone="warn" title="Audit logging is off">
        Nothing is being recorded. Set <code>AUDIT__LEVEL=metadata</code> (IDs and digests) or{" "}
        <code>AUDIT__LEVEL=full</code> (also the query and answer text) on the API and restart it.
        Events recorded earlier are still listed below.
      </Callout>
    );
  }
  return (
    <p className="text-sm text-slate-500">
      Recording at level <strong>{status.level}</strong>
      {status.level === "metadata" && " (IDs and SHA-256 digests, no query or answer text)"} · when a
      write fails: <strong>{status.fail_mode === "deny" ? "refuse the request" : "log and continue"}</strong> ·
      retention: <strong>{status.retention_days ? `${status.retention_days} days` : "forever"}</strong>
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
  const set = (key: keyof typeof EMPTY_FORM) => (e: { target: { value: string } }) =>
    setForm({ ...form, [key]: e.target.value });

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
    <form onSubmit={submit} className="card mb-4 grid grid-cols-4 gap-3 p-4">
      <div>
        <label className="label">Actor</label>
        <input className="input" value={form.actor} onChange={set("actor")} placeholder="exact subject" />
      </div>
      <div>
        <label className="label">Action</label>
        <select className="input" value={form.action} onChange={set("action")}>
          <option value="">Any</option>
          {ACTIONS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="label">Outcome</label>
        <select className="input" value={form.outcome} onChange={set("outcome")}>
          <option value="">Any</option>
          {OUTCOMES.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="label">Request ID</label>
        <input className="input" value={form.request_id} onChange={set("request_id")} />
      </div>
      <div>
        <label className="label">Knowledge base ID</label>
        <input className="input" value={form.knowledge_base_id} onChange={set("knowledge_base_id")} />
      </div>
      <div>
        <label className="label">Assistant ID</label>
        <input className="input" value={form.application_id} onChange={set("application_id")} />
      </div>
      <div>
        <label className="label">From</label>
        <input className="input" type="datetime-local" value={form.since} onChange={set("since")} />
      </div>
      <div>
        <label className="label">Until</label>
        <input className="input" type="datetime-local" value={form.until} onChange={set("until")} />
      </div>
      <div className="col-span-4 flex justify-end gap-2">
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

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-2 py-1.5 text-sm">
      <div className="text-slate-500">{label}</div>
      <div className="col-span-2 break-words text-slate-800">{children ?? "—"}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</div>
      <div className="divide-y divide-slate-100">{children}</div>
    </div>
  );
}

function EventDetail({ event }: { event: AuditEvent }) {
  const isQuery = event.action.startsWith("query.");
  return (
    <div className="max-h-[70vh] space-y-5 overflow-y-auto pr-1">
      <Section title="Event">
        <Field label="When">{fmt(event.occurred_at)}</Field>
        <Field label="Action">{ACTION_LABELS[event.action] ?? event.action}</Field>
        <Field label="Outcome">
          <StatusBadge status={event.outcome} />
          {event.status_code !== null && <span className="ml-2 text-slate-500">HTTP {event.status_code}</span>}
          {event.error_code && <span className="ml-2 font-mono text-xs text-red-600">{event.error_code}</span>}
        </Field>
        <Field label="Request ID">
          <span className="font-mono text-xs">{event.request_id}</span>
        </Field>
        <Field label="Route">{target(event)}</Field>
        {event.resource_id && (
          <Field label="Resource">
            {event.resource_type} <span className="font-mono text-xs">{event.resource_id}</span>
          </Field>
        )}
        {event.latency_ms !== null && <Field label="Latency">{event.latency_ms} ms</Field>}
      </Section>

      <Section title="Who">
        <Field label="Subject">{event.actor_subject ?? <span className="text-slate-400">not authenticated</span>}</Field>
        <Field label="Signed in with">{event.auth_method}</Field>
        <Field label="Roles">{event.actor_roles?.join(", ") || "—"}</Field>
        <Field label="Groups">{event.actor_groups?.join(", ") || "—"}</Field>
        {event.api_key_id && (
          <Field label="API key">
            <span className="font-mono text-xs">{event.api_key_id}</span>
          </Field>
        )}
        {event.application_id && (
          <Field label="Assistant">
            <span className="font-mono text-xs">{event.application_id}</span>
          </Field>
        )}
        <Field label="Source IP">{event.source_ip}</Field>
        <Field label="User agent">{event.user_agent}</Field>
      </Section>

      {isQuery && (
        <Section title="Knowledge scope">
          <Field label="Requested">{kbList(event.requested_kb_ids, "every knowledge base (global search)")}</Field>
          <Field label="Permitted">{kbList(event.permitted_kb_ids, "unrestricted")}</Field>
          <Field label="Dropped by grants">{kbList(event.dropped_kb_ids, "none")}</Field>
          {event.cache_hit !== null && <Field label="Cache hit">{event.cache_hit ? "yes" : "no"}</Field>}
          {event.model && <Field label="Model">{event.model}</Field>}
        </Section>
      )}

      {isQuery && (
        <Section title="Retrieved">
          {event.retrieved === null ? (
            <p className="py-1.5 text-sm text-slate-400">No retrieval ran for this request.</p>
          ) : event.retrieved.length === 0 ? (
            <p className="py-1.5 text-sm text-slate-400">Nothing was retrieved.</p>
          ) : (
            <table className="w-full text-xs">
              <thead className="text-left text-slate-400">
                <tr>
                  <th className="py-1 font-medium">Chunk</th>
                  <th className="py-1 font-medium">Document</th>
                  <th className="py-1 font-medium">Knowledge base</th>
                  <th className="py-1 text-right font-medium">Score</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {event.retrieved.map((r, i) => (
                  <tr key={`${r.chunk_id}-${i}`} className="border-t border-slate-100">
                    <td className="py-1" title={r.chunk_id ?? ""}>{short(r.chunk_id)}</td>
                    <td className="py-1" title={r.document_id ?? ""}>{short(r.document_id)}</td>
                    <td className="py-1" title={r.knowledge_base_id ?? ""}>{short(r.knowledge_base_id)}</td>
                    <td className="py-1 text-right">{r.score?.toFixed(3) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      )}

      {isQuery && (
        <Section title="Exchange">
          <Field label="Query">
            {event.query_text ?? <span className="text-slate-400">not stored (level metadata)</span>}
          </Field>
          <Field label="Query SHA-256">
            <span className="font-mono text-xs">{event.query_sha256}</span>
          </Field>
          {event.action === "query.answer" && (
            <>
              <Field label="Answer">
                {event.answer_text ?? <span className="text-slate-400">not stored (level metadata)</span>}
              </Field>
              <Field label="Answer SHA-256">
                <span className="font-mono text-xs">{event.answer_sha256}</span>
              </Field>
            </>
          )}
        </Section>
      )}

      {event.details && (
        <Section title="Details">
          <pre className="overflow-x-auto rounded bg-slate-50 p-3 text-xs text-slate-700">
            {JSON.stringify(event.details, null, 2)}
          </pre>
        </Section>
      )}
    </div>
  );
}

function ExportButtons({ filters }: { filters: AuditFilters }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const download = async (format: "ndjson" | "csv") => {
    setBusy(format);
    setError(null);
    try {
      const { blob, filename } = await auditApi.export(filters, format);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename ?? `audit.${format}`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex gap-2">
        <button className="btn btn-secondary whitespace-nowrap" disabled={busy !== null} onClick={() => download("csv")}>
          {busy === "csv" ? "Exporting…" : "Export CSV"}
        </button>
        <button className="btn btn-secondary whitespace-nowrap" disabled={busy !== null} onClick={() => download("ndjson")}>
          {busy === "ndjson" ? "Exporting…" : "Export NDJSON"}
        </button>
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
      <p className="text-xs text-slate-400">Exports use the current filters and are themselves audited.</p>
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

  return (
    <div>
      <PageHeader
        title="Audit log"
        description="Who asked what, which knowledge they were allowed to search, what was retrieved and answered, and every change to the control plane."
        action={<ExportButtons filters={filters} />}
      />
      <div className="mb-4">
        {status.data && <RecordingStatus status={status.data} />}
        {status.isError && <ErrorState message={(status.error as Error).message} />}
      </div>

      <FilterBar onApply={setFilters} />

      {events.isLoading && <LoadingState />}
      {events.isError && <ErrorState message={(events.error as Error).message} />}
      {events.data && rows.length === 0 && (
        <EmptyState title="No audit events" description="Nothing matches these filters yet." />
      )}
      {rows.length > 0 && (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-400">
              <tr>
                <th className="px-5 py-3 font-medium">When</th>
                <th className="px-4 py-3 font-medium">Actor</th>
                <th className="px-4 py-3 font-medium">Action</th>
                <th className="px-4 py-3 font-medium">Target</th>
                <th className="px-4 py-3 font-medium">Outcome</th>
                <th className="px-4 py-3 text-right font-medium">Retrieved</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr
                  key={e.id}
                  onClick={() => setSelected(e)}
                  className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                >
                  <td className="whitespace-nowrap px-5 py-3 text-xs text-slate-500">{fmt(e.occurred_at)}</td>
                  <td className="px-4 py-3">
                    <div className="max-w-[14rem] truncate" title={e.actor_subject ?? ""}>
                      {e.actor_subject ?? <span className="text-slate-400">anonymous</span>}
                    </div>
                    {e.auth_method && <div className="text-xs text-slate-400">{e.auth_method}</div>}
                  </td>
                  <td className="px-4 py-3">{ACTION_LABELS[e.action] ?? e.action}</td>
                  <td className="px-4 py-3">{target(e)}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={e.outcome} />
                  </td>
                  <td className="px-4 py-3 text-right text-slate-500">
                    {e.cache_hit ? "cached" : e.retrieved === null ? "—" : e.retrieved.length}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {events.hasNextPage && (
            <div className="border-t border-slate-100 p-3 text-center">
              <button
                className="btn btn-secondary"
                disabled={events.isFetchingNextPage}
                onClick={() => events.fetchNextPage()}
              >
                {events.isFetchingNextPage ? "Loading…" : "Load older events"}
              </button>
            </div>
          )}
        </div>
      )}

      <Modal open={selected !== null} onClose={() => setSelected(null)} title="Audit event" size="xl">
        {selected && <EventDetail event={selected} />}
      </Modal>
    </div>
  );
}
