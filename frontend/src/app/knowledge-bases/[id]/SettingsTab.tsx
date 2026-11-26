"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ConfirmModal, Icon, useToast } from "@/components/ui";
import { knowledgeBasesApi } from "@/lib/api/resources";
import type { KnowledgeBase } from "@/lib/api/types";
import { KnowledgeBaseFields, type KnowledgeBaseForm } from "../KnowledgeBaseFields";

export function SettingsTab({ kb }: { kb: KnowledgeBase }) {
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const [form, setForm] = useState<KnowledgeBaseForm>({
    name: kb.name,
    description: kb.description ?? "",
  });
  const [deleting, setDeleting] = useState(false);

  const save = useMutation({
    mutationFn: () => knowledgeBasesApi.update(kb.id, { ...form, name: form.name.trim() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kb", kb.id] });
      qc.invalidateQueries({ queryKey: ["kbs"] });
      toast({ title: "Knowledge Base saved" });
    },
  });
  const remove = useMutation({
    mutationFn: () => knowledgeBasesApi.remove(kb.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kbs"] });
      router.push("/knowledge-bases");
    },
  });

  return (
    <div className="max-w-2xl space-y-6">
      <form
        className="card space-y-4 p-5"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <h2 className="font-display text-lg font-semibold">Settings</h2>
        <KnowledgeBaseFields value={form} onChange={(p) => setForm((f) => ({ ...f, ...p }))} />
        <dl className="grid grid-cols-2 gap-3 rounded-control bg-sunken/60 p-3 text-sm">
          <div>
            <dt className="text-muted">Embedding model</dt>
            <dd className="font-mono text-xs text-fg">{kb.embedding_model}</dd>
          </div>
          <div>
            <dt className="text-muted">Dimension</dt>
            <dd className="font-mono text-xs text-fg">{kb.embedding_dimension}</dd>
          </div>
          <p className="col-span-2 text-xs text-muted">The embedding model is fixed once Documents are indexed, so it cannot be changed here.</p>
        </dl>
        {save.isError && (
          <p role="alert" className="text-sm text-danger">
            {(save.error as Error).message}
          </p>
        )}
        <button type="submit" className="btn btn-primary" disabled={save.isPending || !form.name.trim()}>
          {save.isPending ? "Saving…" : "Save changes"}
        </button>
      </form>

      <section className="card border-danger/30 p-5">
        <h2 className="font-display text-lg font-semibold text-danger">Delete this Knowledge Base</h2>
        <p className="mt-1 text-sm text-muted">Removes the Knowledge Base and every Document in it. Applications that use it can no longer answer from it.</p>
        <button type="button" className="btn btn-danger mt-3" onClick={() => setDeleting(true)}>
          <Icon icon={Trash2} size="sm" />
          Delete Knowledge Base
        </button>
      </section>

      <ConfirmModal
        open={deleting}
        onClose={() => setDeleting(false)}
        title={`Delete ${kb.name}?`}
        confirmLabel="Delete Knowledge Base"
        pending={remove.isPending}
        error={remove.isError ? (remove.error as Error).message : null}
        onConfirm={() => remove.mutate()}
      >
        <p>
          <strong className="text-fg">{kb.name}</strong> and its {kb.document_count} Document{kb.document_count === 1 ? "" : "s"} will be removed.
        </p>
      </ConfirmModal>
    </div>
  );
}
