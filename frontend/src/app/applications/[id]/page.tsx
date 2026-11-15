"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ErrorState, PageHeader, Skeleton, StatusBadge, Tabs } from "@/components/ui";
import { applicationsApi } from "@/lib/api/resources";
import type { Application } from "@/lib/api/types";
import { useRouter } from "next/navigation";
import { DeleteApplicationModal, applicationStatus } from "../DeleteApplication";
import { ApiAccessTab } from "./ApiAccessTab";
import { KnowledgeBasesTab } from "./KnowledgeBasesTab";
import { OverviewTab } from "./OverviewTab";
import { RetrievalTab } from "./RetrievalTab";
import { TestTab } from "./TestTab";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "knowledge-bases", label: "Knowledge Bases" },
  { id: "retrieval", label: "Retrieval" },
  { id: "test", label: "Test" },
  { id: "api", label: "API access" },
];

function DetailSkeleton() {
  return (
    <div role="status" className="space-y-4">
      <span className="sr-only">Loading Application</span>
      <Skeleton className="h-8 w-1/3" />
      <Skeleton className="h-4 w-1/2" />
      <Skeleton className="mt-6 h-9 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

function Detail() {
  const { id } = useParams<{ id: string }>();
  const app = useQuery({ queryKey: ["app", id], queryFn: () => applicationsApi.get(id) });

  if (app.isLoading) return <DetailSkeleton />;
  if (app.isError) {
    return (
      <div>
        <Link href="/applications" className="text-sm text-accent-text underline">
          Back to Applications
        </Link>
        <div className="mt-4">
          <ErrorState title="Could not load this Application" message={(app.error as Error).message} onRetry={() => app.refetch()} />
        </div>
      </div>
    );
  }
  return <ApplicationDetail app={app.data!} />;
}

function ApplicationDetail({ app }: { app: Application }) {
  const router = useRouter();
  const requested = useSearchParams().get("tab");
  const [tab, setTab] = useState(TABS.some((t) => t.id === requested) ? requested! : "overview");
  const [deleting, setDeleting] = useState(false);
  const status = applicationStatus(app);

  return (
    <div>
      <PageHeader
        title={app.name}
        description={app.description || undefined}
        action={<StatusBadge status={status.label} tone={status.tone} />}
      />
      <Tabs tabs={TABS} value={tab} onChange={setTab} />
      <div role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {tab === "overview" && <OverviewTab app={app} onDelete={() => setDeleting(true)} />}
        {tab === "knowledge-bases" && <KnowledgeBasesTab app={app} />}
        {tab === "retrieval" && <RetrievalTab app={app} />}
        {tab === "test" && <TestTab app={app} />}
        {tab === "api" && <ApiAccessTab app={app} />}
      </div>
      <DeleteApplicationModal app={deleting ? app : null} onClose={() => setDeleting(false)} onDeleted={() => router.push("/applications")} />
    </div>
  );
}

export default function ApplicationDetailPage() {
  return (
    <Suspense fallback={<DetailSkeleton />}>
      <Detail />
    </Suspense>
  );
}
