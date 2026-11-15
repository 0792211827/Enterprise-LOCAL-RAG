"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Library, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  ConfirmModal,
  EmptyState,
  ErrorState,
  Icon,
  Modal,
  PageHeader,
  TBody,
  Table,
  TableSkeleton,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui";
import { knowledgeBasesApi } from "@/lib/api/resources";
import type { KnowledgeBase } from "@/lib/api/types";
import { timeAgo } from "@/lib/format";
import { KnowledgeBaseFields, INITIAL_KB_FORM, type KnowledgeBaseForm } from "./KnowledgeBaseFields";

export default function KnowledgeBasesPage() {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<KnowledgeBase | null>(null);
  const list = useQuery({ queryKey: ["kbs"], queryFn: knowledgeBasesApi.list });

  const remove = useMutation({
    mutationFn: (id: string) => knowledgeBasesApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kbs"] });
      setDeleting(null);
    },
  });

  const create = (
    <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
      <Icon icon={Plus} size="sm" />
      New Knowledge Base
    </button>
  );

  return (
    <div>
      <PageHeader title="Knowledge Bases" description="Collections of Documents. Each is indexed on its own, and each Application draws on the ones you attach." action={create} />

      {list.isLoading && <TableSkeleton columns={5} label="Loading Knowledge Bases" />}
      {list.isError && <ErrorState title="Could not load Knowledge Bases" message={(list.error as Error).message} onRetry={() => list.refetch()} />}
      {list.data && list.data.length === 0 && (
        <EmptyState
          icon={Library}
          title="No Knowledge Bases yet"
          description="Create a Knowledge Base, then upload Documents into it or connect a folder to keep it in sync."
          action={create}
        />
      )}
      {list.data && list.data.length > 0 && (
        <Table caption="Knowledge Bases">
          <THead>
            <tr>
              <Th>Name</Th>
              <Th align="right">Documents</Th>
              <Th align="right">Size</Th>
              <Th>Last updated</Th>
              <Th />
            </tr>
          </THead>
          <TBody>
            {list.data.map((kb) => (
              <Tr key={kb.id}>
                <Td>
                  <Link href={`/knowledge-bases/${kb.id}`} className="font-medium text-accent-text hover:underline">
                    {kb.name}
                  </Link>
                  <div className="font-mono text-xs text-subtle">{kb.slug}</div>
                </Td>
                <Td align="right">{kb.document_count}</Td>
                <Td align="right">{kb.chunk_count.toLocaleString()} chunks</Td>
                <Td>{timeAgo(kb.updated_at ?? kb.created_at)}</Td>
                <Td align="right">
                  <button type="button" className="btn btn-ghost btn-sm" aria-label={`Delete ${kb.name}`} onClick={() => setDeleting(kb)}>
                    <Icon icon={Trash2} size="sm" />
                  </button>
                </Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      )}

      <CreateKnowledgeBase open={creating} onClose={() => setCreating(false)} />

      <ConfirmModal
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title={deleting ? `Delete ${deleting.name}?` : "Delete Knowledge Base"}
        confirmLabel="Delete Knowledge Base"
        pending={remove.isPending}
        error={remove.isError ? (remove.error as Error).message : null}
        onConfirm={() => deleting && remove.mutate(deleting.id)}
      >
        <p>
          <strong className="text-fg">{deleting?.name}</strong> and its {deleting?.document_count} Document{deleting?.document_count === 1 ? "" : "s"} will be removed. Applications that use it will no longer
          be able to answer from it.
        </p>
      </ConfirmModal>
    </div>
  );
}

function CreateKnowledgeBase({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const router = useRouter();
  const [form, setForm] = useState<KnowledgeBaseForm>(INITIAL_KB_FORM);
  const create = useMutation({
    mutationFn: () => knowledgeBasesApi.create({ ...form, name: form.name.trim() }),
    onSuccess: (kb) => {
      qc.invalidateQueries({ queryKey: ["kbs"] });
      setForm(INITIAL_KB_FORM);
      onClose();
      router.push(`/knowledge-bases/${kb.id}`);
    },
  });

  return (
    <Modal open={open} onClose={onClose} title="New Knowledge Base">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <KnowledgeBaseFields value={form} onChange={(p) => setForm((f) => ({ ...f, ...p }))} autoFocus />
        {create.isError && (
          <p role="alert" className="text-sm text-danger">
            {(create.error as Error).message}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={create.isPending || !form.name.trim()}>
            {create.isPending ? "Creating…" : "Create Knowledge Base"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
