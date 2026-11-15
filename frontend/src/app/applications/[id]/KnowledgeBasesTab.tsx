"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Callout, useToast } from "@/components/ui";
import { applicationsApi } from "@/lib/api/resources";
import type { Application } from "@/lib/api/types";
import { KnowledgeBasePicker } from "../KnowledgeBasePicker";

export function KnowledgeBasesTab({ app }: { app: Application }) {
  const qc = useQueryClient();
  const toast = useToast();
  const saved = app.knowledge_bases.map((kb) => kb.id);
  const [ids, setIds] = useState<string[]>(saved);
  const changed = ids.length !== saved.length || ids.some((id) => !saved.includes(id));

  const save = useMutation({
    mutationFn: () => applicationsApi.update(app.id, { knowledge_base_ids: ids }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["app", app.id] });
      qc.invalidateQueries({ queryKey: ["apps"] });
      toast({ title: "Knowledge Bases updated" });
    },
  });

  return (
    <div className="max-w-2xl space-y-4">
      <p className="text-sm text-muted">The Documents this Application may draw on. Anyone using it also needs a Grant on each Knowledge Base.</p>
      <KnowledgeBasePicker selected={ids} onChange={setIds} />
      {ids.length === 0 && <Callout tone="warn">With none attached, this Application cannot answer anything.</Callout>}
      {save.isError && (
        <p role="alert" className="text-sm text-danger">
          {(save.error as Error).message}
        </p>
      )}
      <button type="button" className="btn btn-primary" disabled={!changed || save.isPending} onClick={() => save.mutate()}>
        {save.isPending ? "Saving…" : "Save Knowledge Bases"}
      </button>
    </div>
  );
}
