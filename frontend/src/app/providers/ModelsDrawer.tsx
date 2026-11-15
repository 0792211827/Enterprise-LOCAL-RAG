"use client";
import { useQuery } from "@tanstack/react-query";
import { Box } from "lucide-react";
import { Callout, Drawer, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { providersApi } from "@/lib/api/resources";
import type { Provider } from "@/lib/api/types";

/** The models an Inference endpoint says it serves, asked of the endpoint itself. */
export function ModelsDrawer({ provider, onClose }: { provider: Provider; onClose: () => void }) {
  const models = useQuery({ queryKey: ["provider-models", provider.id], queryFn: () => providersApi.models(provider.id), retry: false });

  return (
    <Drawer open onClose={onClose} title={`Models on ${provider.name}`}>
      {models.isLoading && (
        <div role="status" className="space-y-2">
          <span className="sr-only">Asking {provider.name} for its models</span>
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-2/3" />
        </div>
      )}
      {models.isError && <ErrorState title="Could not ask for the models" message={(models.error as Error).message} onRetry={() => models.refetch()} />}
      {models.data && !models.data.reachable && (
        <Callout tone="danger" title="The endpoint did not list its models">
          {models.data.detail || "It could not be reached."}
        </Callout>
      )}
      {models.data?.reachable && models.data.models.length === 0 && <EmptyState icon={Box} title="No models listed" description="The endpoint answered but serves no models. Pull or load one, then look again." />}
      {models.data?.reachable && models.data.models.length > 0 && (
        <>
          <p className="mb-3 text-sm text-muted">
            {models.data.models.length} model{models.data.models.length === 1 ? "" : "s"} served.{" "}
            {models.data.models.some((m) => m.id === provider.model) ? (
              <>
                <code className="font-mono text-xs text-fg">{provider.model}</code> is among them.
              </>
            ) : (
              <>
                <code className="font-mono text-xs text-fg">{provider.model}</code>, the model set for this endpoint, is <strong className="text-danger">not</strong> among them.
              </>
            )}
          </p>
          <ul aria-label="Models" className="divide-y divide-line rounded-panel border border-line">
            {models.data.models.map((m) => (
              <li key={m.id} className="px-4 py-2.5 font-mono text-xs text-fg [overflow-wrap:anywhere]">
                {m.name}
              </li>
            ))}
          </ul>
        </>
      )}
    </Drawer>
  );
}
