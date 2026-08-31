"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
  confirmDialog,
} from "@/components/ui";
import { applicationsApi } from "@/lib/api/resources";

export default function ApplicationsPage() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["apps"], queryFn: applicationsApi.list });
  const remove = useMutation({
    mutationFn: applicationsApi.remove,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["apps"] }),
  });

  return (
    <div>
      <PageHeader
        title="Assistants"
        description="Each assistant is a callable, OpenAI-compatible endpoint over your documents."
        action={
          <Link href="/applications/new" className="btn btn-primary">
            + New Assistant
          </Link>
        }
      />
      {list.isLoading && <LoadingState />}
      {list.isError && <ErrorState message={(list.error as Error).message} />}
      {list.data && list.data.length === 0 && (
        <EmptyState
          title="No assistants yet"
          description="Create an assistant to expose your documents through a chat interface and an API endpoint."
          action={
            <Link href="/applications/new" className="btn btn-primary">
              New Assistant
            </Link>
          }
        />
      )}
      {list.data && list.data.length > 0 && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {list.data.map((app) => (
            <div key={app.id} className="card p-5">
              <div className="flex items-start justify-between">
                <div>
                  <Link
                    href={`/applications/${app.id}`}
                    className="text-lg font-semibold text-brand-700 hover:underline"
                  >
                    {app.name}
                  </Link>
                  <p className="mt-1 text-sm text-slate-500">
                    {app.description || "No description"}
                  </p>
                </div>
                <StatusBadge status={app.rag_strategy} />
              </div>
              <div className="mt-4 flex flex-wrap gap-2 text-xs text-slate-500">
                <span className="rounded bg-slate-100 px-2 py-1">
                  {app.model_configuration?.llm_model ?? "no model"}
                </span>
                <span className="rounded bg-slate-100 px-2 py-1">
                  {app.retrieval_configuration?.mode ?? "hybrid"} · top-k{" "}
                  {app.retrieval_configuration?.top_k ?? "?"}
                </span>
                <span className="rounded bg-slate-100 px-2 py-1">
                  {app.knowledge_bases.length} KB
                </span>
              </div>
              <div className="mt-4 flex gap-2">
                <Link href={`/applications/${app.id}`} className="btn btn-secondary">
                  Open Playground
                </Link>
                <button
                  className="btn btn-danger"
                  onClick={() => {
                    if (confirmDialog(`Delete "${app.name}"?`)) remove.mutate(app.id);
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
