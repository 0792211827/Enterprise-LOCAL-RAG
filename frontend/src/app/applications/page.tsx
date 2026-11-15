"use client";
import { useQuery } from "@tanstack/react-query";
import { AppWindow, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import {
  Chip,
  EmptyState,
  ErrorState,
  Icon,
  PageHeader,
  StatusBadge,
  TBody,
  Table,
  TableSkeleton,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui";
import { applicationsApi } from "@/lib/api/resources";
import type { Application } from "@/lib/api/types";
import { DeleteApplicationModal, applicationStatus } from "./DeleteApplication";

export default function ApplicationsPage() {
  const list = useQuery({ queryKey: ["apps"], queryFn: applicationsApi.list });
  const [deleting, setDeleting] = useState<Application | null>(null);

  const create = (
    <Link href="/applications/new" className="btn btn-primary">
      <Icon icon={Plus} size="sm" />
      New Application
    </Link>
  );

  return (
    <div>
      <PageHeader
        title="Applications"
        description="Each Application answers from the Knowledge Bases you attach, in the chat and through an OpenAI-compatible endpoint."
        action={create}
      />

      {list.isLoading && <TableSkeleton columns={5} label="Loading Applications" />}
      {list.isError && <ErrorState title="Could not load Applications" message={(list.error as Error).message} onRetry={() => list.refetch()} />}
      {list.data && list.data.length === 0 && (
        <EmptyState
          icon={AppWindow}
          title="No Applications yet"
          description="Create an Application to let people ask questions of your Knowledge Bases, in the chat or from their own software."
          action={create}
        />
      )}
      {list.data && list.data.length > 0 && (
        <Table caption="Applications">
          <THead>
            <tr>
              <Th>Name</Th>
              <Th>Knowledge Bases</Th>
              <Th>Model</Th>
              <Th>Status</Th>
              <Th />
            </tr>
          </THead>
          <TBody>
            {list.data.map((app) => {
              const status = applicationStatus(app);
              return (
                <Tr key={app.id}>
                  <Td>
                    <Link href={`/applications/${app.id}`} className="font-medium text-accent-text hover:underline">
                      {app.name}
                    </Link>
                    <div className="font-mono text-xs text-subtle">{app.slug}</div>
                  </Td>
                  <Td>
                    {app.knowledge_bases.length === 0 ? (
                      <span className="text-muted">None attached</span>
                    ) : (
                      <div className="flex flex-wrap gap-1.5">
                        {app.knowledge_bases.map((kb) => (
                          <Chip key={kb.id}>{kb.name}</Chip>
                        ))}
                      </div>
                    )}
                  </Td>
                  <Td mono>{app.model_configuration?.llm_model ?? "—"}</Td>
                  <Td>
                    <StatusBadge status={status.label} tone={status.tone} />
                  </Td>
                  <Td align="right">
                    <button type="button" className="btn btn-ghost btn-sm" aria-label={`Delete ${app.name}`} onClick={() => setDeleting(app)}>
                      <Icon icon={Trash2} size="sm" />
                    </button>
                  </Td>
                </Tr>
              );
            })}
          </TBody>
        </Table>
      )}

      <DeleteApplicationModal app={deleting} onClose={() => setDeleting(null)} />
    </div>
  );
}
