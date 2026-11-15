"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ConfirmModal } from "@/components/ui";
import { applicationsApi } from "@/lib/api/resources";
import type { Application } from "@/lib/api/types";

/** Whether an Application can answer anything: it needs a Knowledge Base attached. */
export function applicationStatus(app: Pick<Application, "knowledge_bases">): { label: string; tone: "ok" | "warn" } {
  return app.knowledge_bases.length > 0 ? { label: "Active", tone: "ok" } : { label: "No Knowledge Base", tone: "warn" };
}

/** Deletion that names the Application it will remove. */
export function DeleteApplicationModal({
  app,
  onClose,
  onDeleted,
}: {
  app: Pick<Application, "id" | "name"> | null;
  onClose: () => void;
  onDeleted?: () => void;
}) {
  const qc = useQueryClient();
  const remove = useMutation({
    mutationFn: (id: string) => applicationsApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["apps"] });
      onClose();
      onDeleted?.();
    },
  });
  return (
    <ConfirmModal
      open={!!app}
      onClose={onClose}
      title={app ? `Delete ${app.name}?` : "Delete Application"}
      confirmLabel="Delete Application"
      pending={remove.isPending}
      error={remove.isError ? (remove.error as Error).message : null}
      onConfirm={() => app && remove.mutate(app.id)}
    >
      <p>
        <strong className="text-fg">{app?.name}</strong> and its API keys will be removed. Anything calling it will stop working. Its Knowledge Bases and Documents are not
        touched.
      </p>
    </ConfirmModal>
  );
}
