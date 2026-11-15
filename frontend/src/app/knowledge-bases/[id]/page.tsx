"use client";
import { useQuery } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { Callout, ErrorState, Icon, MetricCard, PageHeader, Skeleton, Tabs } from "@/components/ui";
import { knowledgeBasesApi } from "@/lib/api/resources";
import type { KnowledgeBase } from "@/lib/api/types";
import { timeAgo } from "@/lib/format";
import { AccessPanel } from "./AccessPanel";
import { ConnectorsTab } from "./ConnectorsTab";
import { ACCEPT, DocumentsTab } from "./DocumentsTab";
import { SettingsTab } from "./SettingsTab";
import { useUpload } from "./useUpload";

const TABS = [
  { id: "documents", label: "Documents" },
  { id: "connectors", label: "Connectors" },
  { id: "access", label: "Access" },
  { id: "settings", label: "Settings" },
];

function DetailSkeleton() {
  return (
    <div role="status" className="space-y-4">
      <span className="sr-only">Loading Knowledge Base</span>
      <Skeleton className="h-8 w-1/3" />
      <Skeleton className="h-4 w-1/2" />
      <Skeleton className="mt-6 h-9 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

function Detail() {
  const { id } = useParams<{ id: string }>();
  // Ingestion finishes after the upload response, so the counters change with no request to hang an invalidation off.
  const kb = useQuery({ queryKey: ["kb", id], queryFn: () => knowledgeBasesApi.get(id), refetchInterval: 10_000 });

  if (kb.isLoading) return <DetailSkeleton />;
  if (kb.isError) {
    return (
      <div>
        <Link href="/knowledge-bases" className="text-sm text-accent-text underline">
          Back to Knowledge Bases
        </Link>
        <div className="mt-4">
          <ErrorState title="Could not load this Knowledge Base" message={(kb.error as Error).message} onRetry={() => kb.refetch()} />
        </div>
      </div>
    );
  }
  return <KnowledgeBaseDetail kb={kb.data!} />;
}

function KnowledgeBaseDetail({ kb }: { kb: KnowledgeBase }) {
  const requested = useSearchParams().get("tab");
  const [tab, setTab] = useState(TABS.some((t) => t.id === requested) ? requested! : "documents");
  const picker = useRef<HTMLInputElement>(null);
  const { upload, uploading, failures, dismissFailures } = useUpload(kb.id);

  return (
    <div>
      <PageHeader
        title={kb.name}
        description={kb.description || undefined}
        action={
          <>
            <input
              ref={picker}
              type="file"
              multiple
              accept={ACCEPT}
              className="sr-only"
              aria-label="Upload Documents"
              tabIndex={-1}
              onChange={(e) => {
                setTab("documents");
                upload(Array.from(e.target.files ?? []));
                e.target.value = "";
              }}
            />
            <button type="button" className="btn btn-primary" disabled={!!uploading} onClick={() => picker.current?.click()}>
              <Icon icon={Upload} size="sm" />
              {uploading ? "Uploading…" : "Upload Documents"}
            </button>
          </>
        }
      />

      <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MetricCard label="Documents" value={kb.document_count} />
        <MetricCard label="Size" value={`${kb.chunk_count.toLocaleString()} chunks`} />
        <MetricCard label="Last updated" value={timeAgo(kb.updated_at ?? kb.created_at)} />
      </div>

      <Tabs tabs={TABS} value={tab} onChange={setTab} />
      <div role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {tab === "documents" && <DocumentsTab kbId={kb.id} onUpload={upload} uploading={uploading} failures={failures} onDismissFailures={dismissFailures} />}
        {tab === "connectors" && <ConnectorsTab />}
        {tab === "access" && (
          <div className="space-y-4">
            <Callout tone="info" title="This tab is being redesigned">
              Grants work as they did. The layout of this tab is rebuilt in a later update.
            </Callout>
            <AccessPanel kbId={kb.id} />
          </div>
        )}
        {tab === "settings" && <SettingsTab kb={kb} />}
      </div>
    </div>
  );
}

export default function KnowledgeBaseDetailPage() {
  return (
    <Suspense fallback={<DetailSkeleton />}>
      <Detail />
    </Suspense>
  );
}
