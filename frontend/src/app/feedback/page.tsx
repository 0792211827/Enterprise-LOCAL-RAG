"use client";
import { useQuery } from "@tanstack/react-query";
import { Download, EyeOff, MessageSquareText, ThumbsDown, ThumbsUp } from "lucide-react";
import { useMemo, useState } from "react";
import {
  Callout,
  Chip,
  EmptyState,
  ErrorState,
  Icon,
  MetricCard,
  PageHeader,
  Skeleton,
  StatusBadge,
  TBody,
  Table,
  TableSkeleton,
  Td,
  Th,
  THead,
  Tr,
  useToast,
} from "@/components/ui";
import { chatAdminApi } from "@/lib/api/resources";
import type { Rating } from "@/lib/api/types";
import { saveBlob } from "@/lib/download";
import { formatDateTime } from "@/lib/format";

function RatingLabel({ rating }: { rating: Rating }) {
  return rating === "up" ? (
    <span className="inline-flex items-center gap-1.5 text-ok">
      <Icon icon={ThumbsUp} size="sm" />
      Helpful
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 text-danger">
      <Icon icon={ThumbsDown} size="sm" />
      Not helpful
    </span>
  );
}

const percent = (part: number, whole: number) => (whole === 0 ? "—" : `${Math.round((part / whole) * 100)}%`);

/** What an Admin may and may not see of Conversations, said before anything else on the page. */
function PrivacyNote() {
  return (
    <Callout tone="info" title="What Admins can see here">
      <p>Ratings and comments, how many Conversations and messages exist, and the exchanges an End user chose to share with Admins.</p>
      <p className="mt-1.5">
        <strong className="font-medium text-fg">Everything else stays private.</strong> No Admin can read a Conversation beyond the exchanges shared with them.
      </p>
    </Callout>
  );
}

export default function FeedbackPage() {
  const toast = useToast();
  const [applicationId, setApplicationId] = useState("");
  const [exporting, setExporting] = useState(false);

  const counts = useQuery({ queryKey: ["chat-admin", "counts"], queryFn: chatAdminApi.counts });
  const summary = useQuery({ queryKey: ["chat-admin", "summary"], queryFn: chatAdminApi.summary });
  const ratings = useQuery({ queryKey: ["chat-admin", "ratings", applicationId], queryFn: () => chatAdminApi.ratings(applicationId || undefined) });
  const shared = useQuery({ queryKey: ["chat-admin", "shared", applicationId], queryFn: () => chatAdminApi.shared(applicationId || undefined) });

  const names = useMemo(() => {
    const byId = new Map<string, string>();
    counts.data?.by_application.forEach((a) => byId.set(a.application_id, a.application_name));
    summary.data?.forEach((a) => byId.set(a.application_id, a.application_name));
    return byId;
  }, [counts.data, summary.data]);
  const nameOf = (id: string) => names.get(id) ?? "Deleted Application";

  // Weakest first: the lowest share of helpful Ratings, so a weak Application is the first row.
  const ranked = useMemo(
    () => [...(summary.data ?? [])].sort((a, b) => a.up / Math.max(1, a.up + a.down) - b.up / Math.max(1, b.up + b.down)),
    [summary.data],
  );
  const totalUp = summary.data?.reduce((n, a) => n + a.up, 0) ?? 0;
  const totalDown = summary.data?.reduce((n, a) => n + a.down, 0) ?? 0;
  const scoped = applicationId ? nameOf(applicationId) : null;

  const exportCandidates = async () => {
    setExporting(true);
    try {
      const { blob, filename } = await chatAdminApi.exportGolden(applicationId);
      saveBlob(blob, filename ?? "golden-candidates.yaml");
      toast({ title: "Golden set candidates exported", description: filename ?? undefined });
    } catch (err) {
      toast({ title: "Could not export", description: (err as Error).message, tone: "danger" });
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="space-y-8">
      <PageHeader title="Feedback" description="How End users rate the answers each Application gives, and the exchanges they chose to share." />

      <PrivacyNote />

      <section aria-labelledby="summary-title" className="space-y-3">
        <h2 id="summary-title" className="font-display text-lg font-semibold">
          Ratings per Application
        </h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {counts.isLoading || summary.isLoading ? (
            Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-24" />)
          ) : (
            <>
              <MetricCard label="Conversations" value={counts.data?.conversations ?? "—"} hint="A count, never their content" />
              <MetricCard label="Messages" value={counts.data?.messages ?? "—"} />
              <MetricCard label="Helpful" value={totalUp} icon={ThumbsUp} />
              <MetricCard label="Not helpful" value={totalDown} icon={ThumbsDown} hint={`${percent(totalDown, totalUp + totalDown)} of Ratings`} />
            </>
          )}
        </div>
        {summary.isLoading && <TableSkeleton columns={5} rows={3} label="Loading Ratings per Application" />}
        {summary.isError && <ErrorState title="Could not load the Rating summary" message={(summary.error as Error).message} onRetry={() => summary.refetch()} />}
        {summary.data && ranked.length === 0 && (
          <EmptyState icon={MessageSquareText} title="No Ratings yet" description="When End users rate an answer, each Application's Ratings are summarised here, weakest first." />
        )}
        {ranked.length > 0 && (
          <Table caption="Ratings per Application, weakest first">
            <THead>
              <tr>
                <Th>Application</Th>
                <Th align="right">Helpful</Th>
                <Th align="right">Not helpful</Th>
                <Th align="right">Helpful share</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </THead>
            <TBody>
              {ranked.map((a) => (
                <Tr key={a.application_id}>
                  <Td>
                    <span className="font-medium">{a.application_name}</span>
                  </Td>
                  <Td align="right">{a.up}</Td>
                  <Td align="right">{a.down}</Td>
                  <Td align="right">{percent(a.up, a.up + a.down)}</Td>
                  <Td align="right">
                    <button type="button" className="btn btn-ghost btn-sm whitespace-nowrap" aria-label={`Show only ${a.application_name}`} onClick={() => setApplicationId(a.application_id)}>
                      Show only this
                    </button>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        )}
      </section>

      <div className="flex flex-wrap items-center gap-3 rounded-panel border border-line bg-sunken/40 px-4 py-3">
        <label htmlFor="feedback-app" className="text-sm font-medium">
          Application
        </label>
        <select id="feedback-app" className="input !w-auto min-w-[14rem]" value={applicationId} onChange={(e) => setApplicationId(e.target.value)}>
          <option value="">All Applications</option>
          {[...names].map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
        <p className="text-sm text-muted">Applies to the Ratings and Shared exchanges below.</p>
      </div>

      <section aria-labelledby="ratings-title" className="space-y-3">
        <h2 id="ratings-title" className="font-display text-lg font-semibold">
          Ratings
        </h2>
        {ratings.isLoading && <TableSkeleton columns={5} label="Loading Ratings" />}
        {ratings.isError && <ErrorState title="Could not load Ratings" message={(ratings.error as Error).message} onRetry={() => ratings.refetch()} />}
        {ratings.data && ratings.data.length === 0 && (
          <EmptyState icon={MessageSquareText} title="No Ratings" description={scoped ? `Nobody has rated an answer from ${scoped} yet.` : "Nobody has rated an answer yet."} />
        )}
        {!!ratings.data?.length && (
          <Table caption="Ratings">
            <THead>
              <tr>
                <Th>Rating</Th>
                <Th>Application</Th>
                <Th>Comment</Th>
                <Th>Shared</Th>
                <Th>When</Th>
              </tr>
            </THead>
            <TBody>
              {ratings.data.map((r) => (
                <Tr key={r.message_id}>
                  <Td>
                    <RatingLabel rating={r.rating} />
                  </Td>
                  <Td>{nameOf(r.application_id)}</Td>
                  <Td>{r.comment ? <span className="block max-w-md whitespace-pre-wrap [overflow-wrap:anywhere]">{r.comment}</span> : <span className="text-subtle">No comment</span>}</Td>
                  <Td>{r.shared ? <StatusBadge status="Shared" tone="info" /> : <span className="text-muted">Private</span>}</Td>
                  <Td>
                    <span className="whitespace-nowrap text-muted">{formatDateTime(r.rated_at)}</span>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        )}
      </section>

      <section aria-labelledby="shared-title" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 id="shared-title" className="font-display text-lg font-semibold">
              Shared exchanges
            </h2>
            <p className="text-sm text-muted">Question, answer and sources, exactly as the End user chose to share them.</p>
          </div>
          <div className="flex flex-col items-end gap-1">
            <button type="button" className="btn btn-secondary" onClick={exportCandidates} disabled={!applicationId || exporting || !shared.data?.length}>
              <Icon icon={Download} size="sm" />
              {exporting ? "Exporting…" : "Export as Golden set candidates"}
            </button>
            {!applicationId && <p className="text-xs text-muted">Choose one Application above to export its exchanges.</p>}
          </div>
        </div>
        {shared.isLoading && (
          <div role="status" className="space-y-3">
            <span className="sr-only">Loading Shared exchanges</span>
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-28 w-full" />
          </div>
        )}
        {shared.isError && <ErrorState title="Could not load Shared exchanges" message={(shared.error as Error).message} onRetry={() => shared.refetch()} />}
        {shared.data?.length === 0 && (
          <EmptyState icon={EyeOff} title="Nothing shared" description="End users can share one exchange with Admins when they rate an answer. Until they do, there is nothing here to read." />
        )}
        {!!shared.data?.length && (
          <ul aria-label="Shared exchanges" className="space-y-3">
            {shared.data.map((x) => (
              <li key={x.message_id} className="card space-y-3 p-5 text-sm">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
                  <RatingLabel rating={x.rating} />
                  <span>{nameOf(x.application_id)}</span>
                </div>
                {x.comment && <p className="text-muted">“{x.comment}”</p>}
                <div>
                  <div className="text-xs font-medium text-muted">Question</div>
                  <p className="mt-0.5 font-medium text-fg [overflow-wrap:anywhere]">{x.question}</p>
                </div>
                <div>
                  <div className="text-xs font-medium text-muted">Answer</div>
                  <p className="mt-0.5 whitespace-pre-wrap text-fg [overflow-wrap:anywhere]">{x.answer}</p>
                </div>
                {x.sources.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-xs font-medium text-muted">Sources</span>
                    {x.sources.map((s) => (
                      <Chip key={s}>{s}</Chip>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
