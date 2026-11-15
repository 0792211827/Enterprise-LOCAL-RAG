"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plug, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Callout, ConfirmModal, EmptyState, ErrorState, Icon, StatusBadge, TBody, Table, TableSkeleton, Td, Th, THead, Tr } from "@/components/ui";
import { connectorsApi } from "@/lib/api/resources";
import type { Connector } from "@/lib/api/types";
import { timeAgo } from "@/lib/format";
import { ConnectorForm } from "./ConnectorForm";
import { connectorHealth, describeSource, lastError } from "./connectors";

export function ConnectorsTab({ kbId }: { kbId: string }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<{ connector: Connector | null } | null>(null);
  const [deleting, setDeleting] = useState<Connector | null>(null);

  const list = useQuery({ queryKey: ["connectors"], queryFn: connectorsApi.list, refetchInterval: 15_000 });
  const connectors = (list.data ?? []).filter((c) => c.knowledge_base_id === kbId);
  const failed = connectors.filter((c) => connectorHealth(c).failed);

  const remove = useMutation({
    mutationFn: (id: string) => connectorsApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connectors"] });
      setDeleting(null);
    },
  });

  const create = (
    <button type="button" className="btn btn-primary" onClick={() => setForm({ connector: null })}>
      <Icon icon={Plus} size="sm" />
      New Connector
    </button>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-2xl text-sm text-muted">
          A Connector mirrors a folder or bucket into this Knowledge Base, adding, updating and removing Documents as the source changes. How often it runs is set where the Connector job runs, not here.
        </p>
        {create}
      </div>

      {failed.length > 0 && (
        <Callout tone="danger" title={failed.length === 1 ? `${failed[0].name} failed on its last run` : `${failed.length} Connectors failed on their last run`}>
          <ul className="mt-1 space-y-1">
            {failed.map((c) => {
              const reason = lastError(c)?.replace(/\.$/, "");
              return (
                <li key={c.id}>
                  <strong className="text-fg">{c.name}</strong>
                  {reason ? `: ${reason}.` : ": see the Connector job's log for the reason."} Its Documents have not been refreshed since {c.last_run_at ? timeAgo(c.last_run_at) : "it was created"}.
                </li>
              );
            })}
          </ul>
        </Callout>
      )}

      {list.isLoading && <TableSkeleton columns={5} rows={2} label="Loading Connectors" />}
      {list.isError && <ErrorState title="Could not load Connectors" message={(list.error as Error).message} onRetry={() => list.refetch()} />}
      {list.data && connectors.length === 0 && (
        <EmptyState
          icon={Plug}
          title="No Connectors yet"
          description="Connect a mounted folder or an S3-compatible bucket to keep this Knowledge Base in sync without manual upload."
          action={create}
        />
      )}
      {connectors.length > 0 && (
        <Table caption="Connectors">
          <THead>
            <tr>
              <Th>Connector</Th>
              <Th>Source</Th>
              <Th>Schedule</Th>
              <Th>Last run</Th>
              <Th>Health</Th>
              <Th />
            </tr>
          </THead>
          <TBody>
            {connectors.map((c) => {
              const health = connectorHealth(c);
              const source = describeSource(c);
              return (
                <Tr key={c.id}>
                  <Td>
                    <div className="font-medium">{c.name}</div>
                    <div className="text-xs text-muted">{c.source_type === "s3" ? "S3-compatible bucket" : "Mounted folder"}</div>
                  </Td>
                  <Td>
                    <div className="font-mono text-xs [overflow-wrap:anywhere]">{source.primary}</div>
                    {source.secondary && <div className="font-mono text-xs text-muted [overflow-wrap:anywhere]">{source.secondary}</div>}
                  </Td>
                  <Td>{c.enabled ? "Runs with the Connector job" : "Paused"}</Td>
                  <Td>
                    {c.last_run_at ? timeAgo(c.last_run_at) : "Never"}
                    <div className="text-xs text-muted">
                      {c.files_synced} synced{c.files_failed > 0 ? `, ${c.files_failed} failed` : ""}
                    </div>
                  </Td>
                  <Td>
                    <StatusBadge status={health.label} tone={health.tone} />
                    {health.failed && lastError(c) && <p className="mt-1 max-w-xs text-xs text-danger [overflow-wrap:anywhere]">{lastError(c)}</p>}
                  </Td>
                  <Td align="right">
                    <div className="flex justify-end gap-1">
                      <button type="button" className="btn btn-ghost btn-sm" aria-label={`Edit ${c.name}`} onClick={() => setForm({ connector: c })}>
                        <Icon icon={Pencil} size="sm" />
                      </button>
                      <button type="button" className="btn btn-ghost btn-sm" aria-label={`Delete ${c.name}`} onClick={() => setDeleting(c)}>
                        <Icon icon={Trash2} size="sm" />
                      </button>
                    </div>
                  </Td>
                </Tr>
              );
            })}
          </TBody>
        </Table>
      )}

      {form && <ConnectorForm kbId={kbId} connector={form.connector} onClose={() => setForm(null)} />}

      <ConfirmModal
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title={deleting ? `Delete ${deleting.name}?` : "Delete Connector"}
        confirmLabel="Delete Connector"
        pending={remove.isPending}
        error={remove.isError ? (remove.error as Error).message : null}
        onConfirm={() => deleting && remove.mutate(deleting.id)}
      >
        <p>
          <strong className="text-fg">{deleting?.name}</strong> will stop mirroring its source. <strong className="text-fg">Documents it added may be removed</strong> from this Knowledge Base, so check
          the Documents tab afterwards.
        </p>
      </ConfirmModal>
    </div>
  );
}
