"use client";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { StatusBadge } from "@/components/ui";
import { documentsApi } from "@/lib/api/resources";

export type UploadState =
  | "pending"
  | "uploading"
  | "queued"
  | "processing"
  | "completed"
  | "failed"
  | "duplicate";

export interface UploadItem {
  fileName: string;
  state: UploadState;
  documentId?: string;
  error?: string;
}

const STAGES = ["parse", "chunk", "embed", "index"] as const;
const TERMINAL: UploadState[] = ["completed", "failed", "duplicate"];

/**
 * One row per file, owning its own ingestion-job poll.
 *
 * A component per file rather than a loop of hooks in the parent: hooks can't
 * be called in a loop, and this way each poll unmounts cleanly when its file
 * reaches a terminal state.
 */
export function UploadRow({
  item,
  onSettled,
}: {
  item: UploadItem;
  onSettled: (fileName: string, state: UploadState, error?: string) => void;
}) {
  const polling = !!item.documentId && !TERMINAL.includes(item.state);

  const job = useQuery({
    queryKey: ["ingestion-job", item.documentId],
    queryFn: () => documentsApi.job(item.documentId!),
    enabled: polling,
    refetchInterval: polling ? 1500 : false,
    retry: false,
  });

  const status = job.data?.status?.toLowerCase();
  const jobError = job.data?.error;

  // Reporting terminal state is a side effect: doing it inline during render
  // would update the parent mid-render and can loop.
  useEffect(() => {
    if (status === "completed" && item.state !== "completed") {
      onSettled(item.fileName, "completed");
    } else if (status === "failed" && item.state !== "failed") {
      onSettled(item.fileName, "failed", jobError || "Ingestion failed");
    }
  }, [status, jobError, item.state, item.fileName, onSettled]);

  const currentStage = job.data?.stage;
  const stageIndex = currentStage ? STAGES.indexOf(currentStage as (typeof STAGES)[number]) : -1;

  return (
    <li className="flex items-center justify-between gap-4 border-b border-slate-100 py-2.5 last:border-0">
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-slate-700">{item.fileName}</div>
        {item.error && <div className="mt-0.5 text-xs text-red-600">{item.error}</div>}
        {item.state === "duplicate" && (
          <div className="mt-0.5 text-xs text-slate-400">
            Already in this knowledge base — skipped.
          </div>
        )}
        {!TERMINAL.includes(item.state) && item.documentId && (
          <div className="mt-1.5 flex items-center gap-1.5">
            {STAGES.map((stage, i) => (
              <span key={stage} className="flex items-center gap-1">
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    i < stageIndex
                      ? "bg-brand-500"
                      : i === stageIndex
                        ? "animate-pulse bg-brand-500"
                        : "bg-slate-200"
                  }`}
                />
                <span
                  className={`text-[10px] ${
                    i <= stageIndex ? "text-brand-600" : "text-slate-300"
                  }`}
                >
                  {stage}
                </span>
              </span>
            ))}
          </div>
        )}
      </div>
      <StatusBadge status={item.state === "uploading" ? "processing" : item.state} />
    </li>
  );
}
