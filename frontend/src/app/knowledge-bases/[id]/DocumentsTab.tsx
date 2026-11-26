"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, FileUp, Globe, Link2, Lock, RefreshCw, RotateCw, ShieldCheck, Trash2, Upload, X } from "lucide-react";
import { Fragment, useRef, useState } from "react";
import {
  Callout,
  Chip,
  ConfirmModal,
  EmptyState,
  ErrorState,
  Icon,
  StatusBadge,
  TBody,
  Table,
  TableSkeleton,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui";
import { documentsApi } from "@/lib/api/resources";
import type { DocumentItem } from "@/lib/api/types";
import { formatBytes, timeAgo } from "@/lib/format";
import { IngestionStage, isBusy, sourceType, titleCase, useLatestJobs } from "../ingestion";
import { RestrictionEditor } from "./RestrictionEditor";
import type { UploadFailure } from "./useUpload";

// The types the backend accepts (backend/src/services/ingestion/types.py); it decides by content, so this only guides the picker.
export const ACCEPT = ".pdf,.docx,.pptx,.xlsx,.csv,.html,.htm,.txt,.md,.markdown";

export function DocumentsTab({
  kbId,
  onUpload,
  uploading,
  failures,
  onDismissFailures,
}: {
  kbId: string;
  onUpload: (files: File[]) => void;
  uploading: string | null;
  failures: UploadFailure[];
  onDismissFailures: () => void;
}) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const replaceRef = useRef<HTMLInputElement>(null);
  const replacing = useRef<DocumentItem | null>(null);
  const [dragging, setDragging] = useState(false);
  const [restrictingId, setRestrictingId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<DocumentItem | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [address, setAddress] = useState("");

  // Ingestion finishes after the upload response, so the list polls while anything is unfinished.
  const docs = useQuery({
    queryKey: ["docs", kbId],
    queryFn: () => documentsApi.listForKb(kbId),
    refetchInterval: (q) => (q.state.data?.some(isBusy) ? 2_000 : 15_000),
  });
  const jobs = useLatestJobs(docs.data);
  const restricted = useQuery({ queryKey: ["doc-restriction-map", kbId], queryFn: () => documentsApi.restrictionsForKb(kbId) });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["docs", kbId] });
    qc.invalidateQueries({ queryKey: ["kb", kbId] });
    qc.invalidateQueries({ queryKey: ["jobs"] });
  };
  const remove = useMutation({
    mutationFn: (id: string) => documentsApi.remove(id),
    onSuccess: () => {
      setDeleting(null);
      refresh();
    },
  });
  const replace = useMutation({
    mutationFn: ({ id, file }: { id: string; file: File }) => documentsApi.replace(id, file),
    onSuccess: () => {
      setActionError(null);
      refresh();
    },
    onError: (e) => setActionError((e as Error).message),
  });
  const addUrl = useMutation({
    mutationFn: (url: string) => documentsApi.addUrl(kbId, url),
    onSuccess: () => {
      setActionError(null);
      setAddress("");
      refresh();
    },
    onError: (e) => setActionError((e as Error).message),
  });
  const refreshUrl = useMutation({
    mutationFn: (d: DocumentItem) => documentsApi.refreshUrl(d.id),
    onSuccess: (result, d) => {
      setActionError(null);
      setNotice(result.changed ? null : `${d.title} is already up to date: the page has not changed.`);
      refresh();
    },
    onError: (e) => {
      setNotice(null);
      setActionError((e as Error).message);
    },
  });
  const reprocess = useMutation({
    mutationFn: (id: string) => documentsApi.reprocess(id),
    onSuccess: refresh,
    onError: (e) => setActionError((e as Error).message),
  });

  return (
    <div className="space-y-4">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          onUpload(Array.from(e.dataTransfer.files));
        }}
        className={`flex flex-col items-center gap-2 rounded-panel border-2 border-dashed px-6 py-7 text-center transition-colors duration-fast ${
          dragging ? "border-accent bg-accent-soft" : "border-line-strong bg-surface"
        }`}
      >
        <Icon icon={Upload} size="lg" className="text-subtle" />
        <p className="text-sm font-medium text-fg">Drop files here to add them to this Knowledge Base</p>
        <p className="text-xs text-muted">PDF, Word, PowerPoint, Excel, CSV, HTML, plain text or Markdown. Each one is processed and indexed so it can answer questions.</p>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="sr-only"
          aria-label="Choose Documents to upload"
          tabIndex={-1}
          onChange={(e) => {
            onUpload(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
        <button type="button" className="btn btn-secondary mt-1" disabled={!!uploading} onClick={() => fileRef.current?.click()}>
          {uploading ? `Uploading ${uploading}…` : "Choose files"}
        </button>
      </div>

      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (address.trim()) addUrl.mutate(address.trim());
        }}
      >
        <div className="min-w-[16rem] flex-1">
          <label htmlFor="add-url" className="mb-1 block text-xs font-medium text-muted">
            Or add a web page by address
          </label>
          <input
            id="add-url"
            type="url"
            inputMode="url"
            placeholder="https://docs.example.com/guide"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            className="input w-full"
          />
        </div>
        <button type="submit" className="btn btn-secondary" disabled={!address.trim() || addUrl.isPending}>
          <Icon icon={Link2} size="sm" />
          {addUrl.isPending ? "Adding…" : "Add URL"}
        </button>
        <p className="basis-full text-xs text-muted">
          The server fetches just that page, and only from hosts the operator has allowed (INGESTION__URL_ALLOWED_HOSTS).
        </p>
      </form>

      {failures.length > 0 && (
        <Callout tone="danger" title={failures.length === 1 ? "A file was not added" : `${failures.length} files were not added`}>
          <ul className="mt-1 space-y-1">
            {failures.map((f) => (
              <li key={f.file}>
                <strong className="text-fg">{f.file}</strong>: {f.message}
              </li>
            ))}
          </ul>
          <button type="button" className="btn btn-ghost btn-sm mt-2" onClick={onDismissFailures}>
            <Icon icon={X} size="sm" />
            Dismiss
          </button>
        </Callout>
      )}
      <input
        ref={replaceRef}
        type="file"
        accept={ACCEPT}
        className="sr-only"
        aria-label="Choose the replacement file"
        tabIndex={-1}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file && replacing.current) replace.mutate({ id: replacing.current.id, file });
          e.target.value = "";
        }}
      />
      {actionError && <ErrorState title="That did not work" message={actionError} />}
      {notice && (
        <Callout tone="info" title="Nothing to refresh">
          {notice}
        </Callout>
      )}

      {docs.isLoading && <TableSkeleton columns={7} label="Loading Documents" />}
      {docs.isError && <ErrorState title="Could not load Documents" message={(docs.error as Error).message} onRetry={() => docs.refetch()} />}
      {docs.data && docs.data.length === 0 && (
        <EmptyState icon={FileText} title="No Documents yet" description="Upload files above, or connect a folder on the Connectors tab to keep this Knowledge Base in sync." />
      )}
      {docs.data && docs.data.length > 0 && (
        <Table caption="Documents">
          <THead>
            <tr>
              <Th>Document</Th>
              <Th>Type</Th>
              <Th>Ingestion stage</Th>
              <Th>Status</Th>
              <Th align="right">Size</Th>
              <Th>Updated</Th>
              <Th />
            </tr>
          </THead>
          <TBody>
            {docs.data.map((d) => (
              <Fragment key={d.id}>
                <Tr>
                  <Td>
                    <div className="flex items-center gap-2 font-medium">
                      {d.source_kind === "url" && d.source_url ? (
                        <a href={d.source_url} target="_blank" rel="noopener noreferrer" className="underline-offset-2 hover:underline [overflow-wrap:anywhere]">
                          {d.title}
                        </a>
                      ) : (
                        d.title
                      )}
                      {restricted.data?.[d.id] && (
                        <Chip tone="clay" icon={Lock}>
                          Restricted · {restricted.data[d.id].length}
                        </Chip>
                      )}
                    </div>
                  </Td>
                  <Td>
                    {d.source_kind === "url" ? (
                      <span className="inline-flex items-center gap-1">
                        <Icon icon={Globe} size="sm" className="text-subtle" />
                        {d.content_type && sourceType(d.content_type) !== "HTML" ? `Web · ${sourceType(d.content_type)}` : "Web page"}
                      </span>
                    ) : (
                      sourceType(d.content_type)
                    )}
                  </Td>
                  <Td>
                    <IngestionStage doc={d} job={jobs.data?.get(d.id)} />
                  </Td>
                  <Td>
                    <StatusBadge status={titleCase(d.status)} />
                    {(d.error || jobs.data?.get(d.id)?.error) && d.status.toLowerCase() === "failed" && (
                      <p className="mt-1 max-w-xs text-xs text-danger [overflow-wrap:anywhere]">{d.error || jobs.data?.get(d.id)?.error}</p>
                    )}
                    {(d.replacement?.state === "queued" || d.replacement?.state === "processing") && (
                      <p className="mt-1 max-w-xs text-xs text-muted [overflow-wrap:anywhere]">
                        Replacing with {d.replacement.filename ?? "a new file"}; the current content still answers.
                      </p>
                    )}
                    {d.replacement?.state === "failed" && (
                      <p className="mt-1 max-w-xs text-xs text-danger [overflow-wrap:anywhere]">
                        Replacement failed, the previous content still answers: {d.replacement.error ?? d.error}
                      </p>
                    )}
                    {(d.warnings ?? []).map((w) => (
                      <p key={w} className="mt-1 max-w-xs text-xs text-clay-text [overflow-wrap:anywhere]">
                        {w}
                      </p>
                    ))}
                  </Td>
                  <Td align="right">{formatBytes(d.file_size_bytes)}</Td>
                  <Td>{timeAgo(d.updated_at ?? d.created_at)}</Td>
                  <Td align="right">
                    <div className="flex justify-end gap-1">
                      <button type="button" className="btn btn-ghost btn-sm" aria-label={`Restrict ${d.title}`} aria-expanded={restrictingId === d.id} onClick={() => setRestrictingId(restrictingId === d.id ? null : d.id)}>
                        <Icon icon={ShieldCheck} size="sm" />
                      </button>
                      {d.source_kind === "url" ? (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          aria-label={`Refresh ${d.title}`}
                          disabled={isBusy(d) || d.status.toLowerCase() === "failed"}
                          onClick={() => refreshUrl.mutate(d)}
                        >
                          <Icon icon={RefreshCw} size="sm" />
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-ghost btn-sm"
                          aria-label={`Replace ${d.title}`}
                          disabled={isBusy(d)}
                          onClick={() => {
                            replacing.current = d;
                            replaceRef.current?.click();
                          }}
                        >
                          <Icon icon={FileUp} size="sm" />
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        aria-label={`${d.status.toLowerCase() === "failed" || d.replacement?.state === "failed" ? "Retry" : "Reprocess"} ${d.title}`}
                        disabled={isBusy(d)}
                        onClick={() => reprocess.mutate(d.id)}
                      >
                        <Icon icon={RotateCw} size="sm" />
                      </button>
                      <button type="button" className="btn btn-ghost btn-sm" aria-label={`Delete ${d.title}`} onClick={() => setDeleting(d)}>
                        <Icon icon={Trash2} size="sm" />
                      </button>
                    </div>
                  </Td>
                </Tr>
                {restrictingId === d.id && (
                  <tr className="bg-sunken/40">
                    <td colSpan={7} className="px-5 py-4">
                      <RestrictionEditor kbId={kbId} documentId={d.id} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </TBody>
        </Table>
      )}

      <ConfirmModal
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title={deleting ? `Delete ${deleting.title}?` : "Delete Document"}
        confirmLabel="Delete Document"
        pending={remove.isPending}
        error={remove.isError ? (remove.error as Error).message : null}
        onConfirm={() => deleting && remove.mutate(deleting.id)}
      >
        <p>
          <strong className="text-fg">{deleting?.title}</strong> and its indexed passages will be removed. Answers will no longer cite it.
        </p>
      </ConfirmModal>
    </div>
  );
}
