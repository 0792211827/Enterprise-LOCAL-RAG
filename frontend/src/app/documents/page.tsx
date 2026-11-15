"use client";
import { useQueries, useQuery } from "@tanstack/react-query";
import { FileText, Search } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { EmptyState, ErrorState, Icon, PageHeader, StatusBadge, TBody, Table, TableSkeleton, Td, Th, THead, Tr } from "@/components/ui";
import { documentsApi, knowledgeBasesApi } from "@/lib/api/resources";
import type { DocumentItem } from "@/lib/api/types";
import { formatBytes, timeAgo } from "@/lib/format";
import { IngestionStage, titleCase, useLatestJobs } from "../knowledge-bases/ingestion";

const STATUSES = ["all", "queued", "processing", "completed", "failed"];

/** Every Document across every Knowledge Base, for finding one. Uploading happens inside a Knowledge Base. */
export default function DocumentsPage() {
  const [search, setSearch] = useState("");
  const [kbFilter, setKbFilter] = useState("all");
  const [status, setStatus] = useState("all");

  const kbs = useQuery({ queryKey: ["kbs"], queryFn: knowledgeBasesApi.list });
  const lists = useQueries({
    queries: (kbs.data ?? []).map((kb) => ({
      queryKey: ["docs", kb.id],
      queryFn: () => documentsApi.listForKb(kb.id),
      refetchInterval: 15_000,
    })),
  });

  const all: { doc: DocumentItem; kbName: string }[] = [];
  (kbs.data ?? []).forEach((kb, i) => (lists[i]?.data ?? []).forEach((doc) => all.push({ doc, kbName: kb.name })));
  const jobs = useLatestJobs(all.map((r) => r.doc));

  const loading = kbs.isLoading || lists.some((l) => l.isLoading);
  const failure = kbs.error ?? lists.find((l) => l.error)?.error;
  const q = search.trim().toLowerCase();
  const shown = all.filter(
    ({ doc, kbName }) =>
      (kbFilter === "all" || doc.knowledge_base_id === kbFilter) &&
      (status === "all" || doc.status.toLowerCase() === status) &&
      (!q || doc.title.toLowerCase().includes(q) || kbName.toLowerCase().includes(q)),
  );

  return (
    <div>
      <PageHeader
        title="Documents"
        description="Every Document across your Knowledge Bases. To add one, open its Knowledge Base and upload it there."
        action={
          <Link href="/knowledge-bases" className="btn btn-secondary">
            Open Knowledge Bases
          </Link>
        }
      />

      <div className="mb-4 flex flex-wrap gap-3">
        <div className="relative min-w-[14rem] flex-1">
          <label htmlFor="doc-search" className="sr-only">
            Search Documents
          </label>
          <Icon icon={Search} size="sm" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-subtle" />
          <input id="doc-search" type="search" className="input pl-9" placeholder="Search by title" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <div>
          <label htmlFor="doc-kb" className="sr-only">
            Knowledge Base
          </label>
          <select id="doc-kb" className="input" value={kbFilter} onChange={(e) => setKbFilter(e.target.value)}>
            <option value="all">All Knowledge Bases</option>
            {(kbs.data ?? []).map((kb) => (
              <option key={kb.id} value={kb.id}>
                {kb.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="doc-status" className="sr-only">
            Status
          </label>
          <select id="doc-status" className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s === "all" ? "Any status" : titleCase(s)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {loading && <TableSkeleton columns={6} label="Loading Documents" />}
      {!loading && failure && (
        <ErrorState
          title="Could not load Documents"
          message={(failure as Error).message}
          onRetry={() => {
            kbs.refetch();
            lists.forEach((l) => l.refetch());
          }}
        />
      )}
      {!loading && !failure && all.length === 0 && (
        <EmptyState
          icon={FileText}
          title="No Documents yet"
          description="Documents live inside a Knowledge Base. Open one and upload files, or connect a folder."
          action={
            <Link href="/knowledge-bases" className="btn btn-primary">
              Open Knowledge Bases
            </Link>
          }
        />
      )}
      {!loading && !failure && all.length > 0 && shown.length === 0 && (
        <EmptyState icon={Search} title="No Documents match" description="Try a different title, Knowledge Base or status." />
      )}
      {!loading && !failure && shown.length > 0 && (
        <Table caption="Documents across all Knowledge Bases">
          <THead>
            <tr>
              <Th>Document</Th>
              <Th>Knowledge Base</Th>
              <Th>Ingestion stage</Th>
              <Th>Status</Th>
              <Th align="right">Size</Th>
              <Th>Updated</Th>
            </tr>
          </THead>
          <TBody>
            {shown.map(({ doc, kbName }) => (
              <Tr key={doc.id}>
                <Td>
                  <span className="font-medium">{doc.title}</span>
                </Td>
                <Td>
                  <Link href={`/knowledge-bases/${doc.knowledge_base_id}`} className="text-accent-text hover:underline">
                    {kbName}
                  </Link>
                </Td>
                <Td>
                  <IngestionStage doc={doc} job={jobs.data?.get(doc.id)} />
                </Td>
                <Td>
                  <StatusBadge status={titleCase(doc.status)} />
                  {doc.status.toLowerCase() === "failed" && doc.error && <p className="mt-1 max-w-xs text-xs text-danger [overflow-wrap:anywhere]">{doc.error}</p>}
                </Td>
                <Td align="right">{formatBytes(doc.file_size_bytes)}</Td>
                <Td>{timeAgo(doc.updated_at ?? doc.created_at)}</Td>
              </Tr>
            ))}
          </TBody>
        </Table>
      )}
    </div>
  );
}
