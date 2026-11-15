import { connectorHealth, lastError } from "@/app/knowledge-bases/[id]/connectors";
import type { Application, ApplicationRatings, ComponentHealth, Connector, IngestionJob } from "@/lib/api/types";

// What needs an Admin, worked out from what the API already reports. A signal the API does not expose is
// not guessed at: with no data for a check, that check contributes nothing and the Dashboard says so.

export interface AttentionItem {
  id: string;
  tone: "danger" | "warn";
  title: string;
  detail?: string;
  /** Where the problem is fixed. */
  href: string;
  action: string;
}

export interface Signals {
  services?: ComponentHealth[];
  jobs?: IngestionJob[];
  connectors?: Connector[];
  applications?: Application[];
  ratings?: ApplicationRatings[];
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const settled = (status: string) => status.toLowerCase();

/** A Document's newest ingestion job is the one that says how it is now; an old failure it has since recovered from is history. */
export function latestJobs(jobs: IngestionJob[]): IngestionJob[] {
  const latest = new Map<string, IngestionJob>();
  for (const job of jobs) {
    const seen = latest.get(job.document_id);
    if (!seen || (job.created_at ?? "") > (seen.created_at ?? "")) latest.set(job.document_id, job);
  }
  return [...latest.values()];
}

/** A rating signal worth a look: thumbs-down at least as common as thumbs-up. */
export const lowRated = (r: ApplicationRatings) => r.down > 0 && r.down >= r.up;

export function attentionItems({ services, jobs, connectors, applications, ratings }: Signals): AttentionItem[] {
  const items: AttentionItem[] = [];

  for (const c of services ?? []) {
    if (["healthy", "ok", "disabled"].includes(c.status.toLowerCase())) continue;
    items.push({
      id: `service-${c.name}`,
      tone: "danger",
      title: `${c.name} is ${c.status.toLowerCase()}`,
      detail: c.detail ?? undefined,
      href: "/system",
      action: "Open System",
    });
  }

  const failedJobs = latestJobs(jobs ?? []).filter((j) => settled(j.status) === "failed");
  if (failedJobs.length > 0) {
    const newest = [...failedJobs].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))[0];
    const knowledgeBases = new Set(failedJobs.map((j) => j.knowledge_base_id));
    items.push({
      id: "ingestion-failed",
      tone: "danger",
      title: `${plural(failedJobs.length, "Document")} failed to ingest`,
      detail: newest.error ?? undefined,
      href: knowledgeBases.size === 1 ? `/knowledge-bases/${newest.knowledge_base_id}` : "/documents",
      action: knowledgeBases.size === 1 ? "Open the Knowledge Base" : "Open Documents",
    });
  }

  for (const c of connectors ?? []) {
    if (!connectorHealth(c).failed) continue;
    items.push({
      id: `connector-${c.id}`,
      tone: "danger",
      title: `Connector ${c.name} failed on its last run`,
      detail: lastError(c) ?? undefined,
      href: `/knowledge-bases/${c.knowledge_base_id}?tab=connectors`,
      action: "Open Connectors",
    });
  }

  for (const a of applications ?? []) {
    if (a.knowledge_bases.length > 0) continue;
    items.push({
      id: `application-${a.id}`,
      tone: "warn",
      title: `${a.name} has no Knowledge Base`,
      detail: "It has nothing to answer from until one is attached.",
      href: `/applications/${a.id}?tab=knowledge-bases`,
      action: "Attach a Knowledge Base",
    });
  }

  for (const r of ratings ?? []) {
    if (!lowRated(r)) continue;
    items.push({
      id: `rating-${r.application_id}`,
      tone: "warn",
      title: `${r.application_name} has ${plural(r.down, "low-rated answer")}`,
      detail: `${r.down} marked unhelpful, ${r.up} helpful.`,
      href: "/feedback",
      action: "Review feedback",
    });
  }

  return items.sort((a, b) => Number(b.tone === "danger") - Number(a.tone === "danger"));
}
