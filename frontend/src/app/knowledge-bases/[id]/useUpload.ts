"use client";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";
import { documentsApi } from "@/lib/api/resources";

export interface UploadFailure {
  file: string;
  message: string;
}

/** How many files are sent at once. The API answers each upload at once and ingests a couple at a time itself, so this only bounds the requests. */
export const UPLOAD_PARALLELISM = 2;

/**
 * Uploads Documents into one Knowledge Base, a couple at a time. Each file is its own request, so one refusal never
 * stops the others; what the API accepts then shows up in the Documents table with its own ingestion status, and only
 * refusals are kept here. Files added while a batch is running join it.
 */
export function useUpload(kbId: string) {
  const qc = useQueryClient();
  const [failures, setFailures] = useState<UploadFailure[]>([]);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const waiting = useRef<File[]>([]);
  const workers = useRef(0);
  const counts = useRef({ done: 0, total: 0 });

  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["docs", kbId] });
    qc.invalidateQueries({ queryKey: ["kb", kbId] });
    qc.invalidateQueries({ queryKey: ["jobs"] });
  }, [kbId, qc]);

  const work = useCallback(async () => {
    for (let file = waiting.current.shift(); file; file = waiting.current.shift()) {
      try {
        await documentsApi.upload(kbId, file);
      } catch (e) {
        const message = (e as Error).message;
        const duplicate = message.toLowerCase().includes("identical document");
        setFailures((all) => [...all, { file: file!.name, message: duplicate ? "Already in this Knowledge Base, so it was skipped." : message }]);
      }
      counts.current.done += 1;
      setProgress({ ...counts.current });
      refresh();
    }
    workers.current -= 1;
    if (workers.current === 0) {
      counts.current = { done: 0, total: 0 };
      setProgress(null);
    }
  }, [kbId, refresh]);

  const upload = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;
      if (workers.current === 0) setFailures([]);
      waiting.current.push(...files);
      counts.current.total += files.length;
      setProgress({ ...counts.current });
      while (workers.current < UPLOAD_PARALLELISM && workers.current < waiting.current.length) {
        workers.current += 1;
        void work();
      }
    },
    [work],
  );

  const uploading = progress ? (progress.total === 1 ? "1 file" : `${Math.min(progress.done + 1, progress.total)} of ${progress.total} files`) : null;
  return { upload, uploading, failures, dismissFailures: () => setFailures([]) };
}
