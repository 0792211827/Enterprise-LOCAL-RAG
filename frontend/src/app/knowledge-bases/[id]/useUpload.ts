"use client";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";
import { documentsApi } from "@/lib/api/resources";

export interface UploadFailure {
  file: string;
  message: string;
}

/**
 * Uploads Documents into one Knowledge Base, one after another. Sequential on purpose: ingestion runs on
 * the API's event loop and embedding is CPU-heavy, so parallel uploads stall the whole API. What the API
 * accepts then shows up in the Documents table with its own ingestion stage; only refusals are kept here.
 */
export function useUpload(kbId: string) {
  const qc = useQueryClient();
  const [failures, setFailures] = useState<UploadFailure[]>([]);
  const [uploading, setUploading] = useState<string | null>(null);
  const running = useRef(false);

  const upload = useCallback(
    async (files: File[]) => {
      if (running.current || files.length === 0) return;
      running.current = true;
      setFailures([]);
      for (const file of files) {
        setUploading(file.name);
        try {
          await documentsApi.upload(kbId, file);
        } catch (e) {
          const message = (e as Error).message;
          const duplicate = message.toLowerCase().includes("identical document");
          setFailures((all) => [...all, { file: file.name, message: duplicate ? "Already in this Knowledge Base, so it was skipped." : message }]);
        }
        qc.invalidateQueries({ queryKey: ["docs", kbId] });
        qc.invalidateQueries({ queryKey: ["kb", kbId] });
        qc.invalidateQueries({ queryKey: ["jobs"] });
      }
      setUploading(null);
      running.current = false;
    },
    [kbId, qc],
  );

  return { upload, uploading, failures, dismissFailures: () => setFailures([]) };
}
