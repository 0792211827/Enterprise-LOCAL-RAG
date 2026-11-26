"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { ErrorState, Skeleton } from "@/components/ui";
import { knowledgeBasesApi } from "@/lib/api/resources";

/** Choose which Knowledge Bases an Application answers from. */
export function KnowledgeBasePicker({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const kbs = useQuery({ queryKey: ["kbs"], queryFn: knowledgeBasesApi.list });

  if (kbs.isLoading) {
    return (
      <div role="status" className="space-y-2">
        <span className="sr-only">Loading Knowledge Bases</span>
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }
  if (kbs.isError) return <ErrorState title="Could not load Knowledge Bases" message={(kbs.error as Error).message} onRetry={() => kbs.refetch()} />;
  if (!kbs.data?.length) {
    return (
      <p className="rounded-panel border border-dashed border-line-strong px-4 py-5 text-sm text-muted">
        There are no Knowledge Bases yet.{" "}
        <Link href="/knowledge-bases" className="text-accent-text underline">
          Create one and upload Documents
        </Link>{" "}
        first; an Application with none attached cannot answer anything.
      </p>
    );
  }
  const toggle = (id: string) => onChange(selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id]);
  return (
    <ul className="divide-y divide-line rounded-panel border border-line bg-surface">
      {kbs.data.map((kb) => (
        <li key={kb.id}>
          <label className="flex cursor-pointer items-start gap-3 px-4 py-3 hover:bg-sunken/40">
            <input type="checkbox" className="mt-1" checked={selected.includes(kb.id)} onChange={() => toggle(kb.id)} />
            <span className="min-w-0">
              <span className="block text-sm font-medium text-fg">{kb.name}</span>
              <span className="block text-xs text-muted">
                {kb.document_count} Document{kb.document_count === 1 ? "" : "s"}
                {kb.description ? ` · ${kb.description}` : ""}
              </span>
            </span>
          </label>
        </li>
      ))}
    </ul>
  );
}
