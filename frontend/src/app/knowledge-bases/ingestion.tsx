"use client";
import { useQuery } from "@tanstack/react-query";
import { documentsApi } from "@/lib/api/resources";
import type { DocumentItem, IngestionJob } from "@/lib/api/types";

const STAGES = ["parse", "chunk", "embed", "index"] as const;
const STAGE_LABEL: Record<string, string> = {
  fetch: "Fetching",
  parse: "Parsing",
  extract: "Extracting",
  chunk: "Preparing",
  embed: "Embedding",
  index: "Indexing",
};

export const isSettled = (status: string) => ["completed", "failed"].includes(status.toLowerCase());

/** Still being ingested: the Document itself, or a replacement for it. */
export const isBusy = (d: DocumentItem) => !isSettled(d.status) || d.replacement?.state === "queued" || d.replacement?.state === "processing";

export const titleCase = (status: string) => status.charAt(0).toUpperCase() + status.slice(1).toLowerCase();

const TYPE_LABELS: [RegExp, string][] = [
  [/pdf/, "PDF"],
  [/wordprocessingml/, "DOCX"],
  [/presentationml/, "PPTX"],
  [/spreadsheetml/, "XLSX"],
  [/csv/, "CSV"],
  [/html/, "HTML"],
  [/markdown/, "MD"],
  [/^text\/plain/, "TXT"],
];

/** What kind of source a Document came from, in the words an admin uses. */
export const sourceType = (contentType: string | null) => TYPE_LABELS.find(([pattern]) => pattern.test(contentType ?? ""))?.[1] ?? "File";

/** The newest ingestion job of each Document, polled while anything is still being ingested. */
export function useLatestJobs(documents: DocumentItem[] | undefined) {
  const busy = documents?.some(isBusy) ?? false;
  return useQuery({
    queryKey: ["jobs"],
    queryFn: async () => {
      const latest = new Map<string, IngestionJob>();
      for (const job of await documentsApi.jobs()) {
        const seen = latest.get(job.document_id);
        if (!seen || (job.created_at ?? "") > (seen.created_at ?? "")) latest.set(job.document_id, job);
      }
      return latest;
    },
    refetchInterval: busy ? 2_000 : 15_000,
  });
}

/** Where a Document is in ingestion: a trail of stages while it runs, the stage it failed in, or Done. */
export function IngestionStage({ doc, job }: { doc: DocumentItem; job?: IngestionJob }) {
  const status = doc.status.toLowerCase();
  const stage = job?.stage ?? null;
  if (status === "completed") return <span className="text-muted">Done</span>;
  if (status === "failed") return <span className="text-muted">{stage ? `Stopped while ${(STAGE_LABEL[stage] ?? stage).toLowerCase()}` : "Did not start"}</span>;
  const at = stage ? STAGES.indexOf(stage as (typeof STAGES)[number]) : -1;
  return (
    <div className="flex flex-col gap-1">
      <span className="text-fg">{stage ? (STAGE_LABEL[stage] ?? stage) : "Waiting to start"}</span>
      <span aria-hidden className="flex gap-1">
        {STAGES.map((s, i) => (
          <span key={s} className={`h-1 w-5 rounded-full ${i < at ? "bg-accent" : i === at ? "animate-pulse bg-accent" : "bg-line-strong"}`} />
        ))}
      </span>
    </div>
  );
}
