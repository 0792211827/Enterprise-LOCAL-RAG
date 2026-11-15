"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Box, Cpu, Pencil, Plus, Trash2 } from "lucide-react";
import { Fragment, useState } from "react";
import { Callout, ConfirmModal, EmptyState, ErrorState, Icon, PageHeader, StatusBadge, TBody, Table, TableSkeleton, Td, Th, THead, Tr } from "@/components/ui";
import { providersApi } from "@/lib/api/resources";
import type { Provider } from "@/lib/api/types";
import { ModelsDrawer } from "./ModelsDrawer";
import { KIND_LABELS, ProviderForm, TYPE_LABELS } from "./ProviderForm";
import { describeTest, type TestState } from "./testResult";

export default function ProvidersPage() {
  const qc = useQueryClient();
  const [form, setForm] = useState<{ provider: Provider | null } | null>(null);
  const [deleting, setDeleting] = useState<Provider | null>(null);
  const [listing, setListing] = useState<Provider | null>(null);
  const [tests, setTests] = useState<Record<string, TestState>>({});
  const list = useQuery({ queryKey: ["providers"], queryFn: () => providersApi.list() });

  const remove = useMutation({
    mutationFn: (id: string) => providersApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["providers"] });
      setDeleting(null);
    },
  });
  const test = useMutation({
    mutationFn: (id: string) => providersApi.test(id),
    onSuccess: (result, id) => setTests((t) => ({ ...t, [id]: { result } })),
    onError: (error, id) => setTests((t) => ({ ...t, [id]: { error: (error as Error).message } })),
  });

  const add = (
    <button type="button" className="btn btn-primary" onClick={() => setForm({ provider: null })}>
      <Icon icon={Plus} size="sm" />
      Add Inference endpoint
    </button>
  );

  return (
    <div>
      <PageHeader
        title="Models & Providers"
        description="The Inference endpoints that run language, embedding and vision models. Test one to confirm the address and key work, and list the models it serves."
        action={add}
      />

      {list.isLoading && <TableSkeleton columns={6} label="Loading Inference endpoints" />}
      {list.isError && <ErrorState title="Could not load Inference endpoints" message={(list.error as Error).message} onRetry={() => list.refetch()} />}
      {list.data && list.data.length === 0 && (
        <EmptyState
          icon={Cpu}
          title="No Inference endpoints yet"
          description="Add an Ollama, OpenAI-compatible or HuggingFace endpoint so Applications can generate answers and Knowledge Bases can embed Documents."
          action={add}
        />
      )}
      {list.data && list.data.length > 0 && (
        <Table caption="Inference endpoints">
          <THead>
            <tr>
              <Th>Endpoint</Th>
              <Th>Does</Th>
              <Th>Type</Th>
              <Th>Model</Th>
              <Th>Status</Th>
              <Th />
            </tr>
          </THead>
          <TBody>
            {list.data.map((p) => {
              const testing = test.isPending && test.variables === p.id;
              const state = tests[p.id];
              const outcome = state && describeTest(state, p);
              return (
                <Fragment key={p.id}>
                  <Tr>
                    <Td>
                      <div className="min-w-[12rem]">
                        <div className="font-medium">{p.name}</div>
                        {p.endpoint && <div className="font-mono text-xs text-muted [overflow-wrap:anywhere]">{p.endpoint}</div>}
                      </div>
                    </Td>
                    <Td>{KIND_LABELS[p.kind] ?? p.kind}</Td>
                    <Td>{TYPE_LABELS[p.provider_type] ?? p.provider_type}</Td>
                    <Td mono>{p.model}</Td>
                    <Td>
                      <StatusBadge status={p.enabled ? "Active" : "Disabled"} />
                    </Td>
                    <Td align="right">
                      <div className="flex justify-end gap-1">
                        <button type="button" className="btn btn-secondary btn-sm" aria-label={`Test ${p.name}`} disabled={testing} onClick={() => test.mutate(p.id)}>
                          {testing ? "Testing…" : "Test"}
                        </button>
                        {p.kind !== "embedding" && (
                          <button type="button" className="btn btn-ghost btn-sm" aria-label={`List models on ${p.name}`} onClick={() => setListing(p)}>
                            <Icon icon={Box} size="sm" />
                          </button>
                        )}
                        <button type="button" className="btn btn-ghost btn-sm" aria-label={`Edit ${p.name}`} onClick={() => setForm({ provider: p })}>
                          <Icon icon={Pencil} size="sm" />
                        </button>
                        <button type="button" className="btn btn-ghost btn-sm" aria-label={`Delete ${p.name}`} onClick={() => setDeleting(p)}>
                          <Icon icon={Trash2} size="sm" />
                        </button>
                      </div>
                    </Td>
                  </Tr>
                  {outcome && (
                    <tr>
                      <td colSpan={6} className="bg-sunken/40 px-4 py-3">
                        <Callout tone={outcome.tone} title={outcome.title}>
                          {outcome.detail && <span className="block font-mono text-xs [overflow-wrap:anywhere]">{outcome.detail}</span>}
                          {outcome.tone === "danger" && <span className="mt-1 block">Check the address and key, then test again.</span>}
                          {outcome.facts.length > 0 && <span className="mt-1 block text-xs">{outcome.facts.join(" · ")}</span>}
                        </Callout>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </TBody>
        </Table>
      )}

      {form && <ProviderForm provider={form.provider} onClose={() => setForm(null)} />}
      {listing && <ModelsDrawer provider={listing} onClose={() => setListing(null)} />}

      <ConfirmModal
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title={deleting ? `Delete ${deleting.name}?` : "Delete Inference endpoint"}
        confirmLabel="Delete endpoint"
        pending={remove.isPending}
        error={remove.isError ? (remove.error as Error).message : null}
        onConfirm={() => deleting && remove.mutate(deleting.id)}
      >
        <p>
          <strong className="text-fg">{deleting?.name}</strong> will be removed from this list. The model server itself is untouched.
        </p>
      </ConfirmModal>
    </div>
  );
}
