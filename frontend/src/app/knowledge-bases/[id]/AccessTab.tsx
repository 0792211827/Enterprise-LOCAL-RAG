"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Callout, Chip, Icon, Skeleton } from "@/components/ui";
import { documentsApi, knowledgeBasesApi, systemApi } from "@/lib/api/resources";
import type { DocumentAccessCheck, DocumentItem, KnowledgeBaseGrantEntry } from "@/lib/api/types";
import { deniesUngranted } from "@/lib/security";
import { PrincipalEditor, sameSet, strip } from "./principals";

// More than the API's default page, so a large Knowledge Base is checked whole; past this the result says it stopped.
const CHECK_LIMIT = 500;

interface Row {
  doc: DocumentItem;
  check: DocumentAccessCheck;
}

interface CheckOutcome {
  principal: string;
  granted: boolean;
  unrestricted: boolean;
  reason: string;
  readable: Row[];
  unreadable: Row[];
  truncated: boolean;
}

async function mapLimit<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

const describePrincipal = (subject: string, groups: string, roles: string) =>
  [subject.trim(), groups.trim() && `groups ${groups.trim()}`, roles.trim() && `roles ${roles.trim()}`].filter(Boolean).join(", ");

/** Who can read what in this Knowledge Base: the check leads, because verifying access is why an Admin comes here. */
export function AccessTab({ kbId }: { kbId: string }) {
  const [stale, setStale] = useState(0);
  return (
    <div className="max-w-4xl space-y-6">
      <AccessCheck kbId={kbId} resetKey={stale} />
      <Grants kbId={kbId} onSaved={() => setStale((n) => n + 1)} />
    </div>
  );
}

function AccessCheck({ resetKey, kbId }: { kbId: string; resetKey: number }) {
  const [subject, setSubject] = useState("");
  const [groups, setGroups] = useState("");
  const [roles, setRoles] = useState("");

  const check = useMutation({
    mutationFn: async (): Promise<CheckOutcome> => {
      const [kbCheck, docs] = await Promise.all([
        knowledgeBasesApi.accessCheck(subject, groups, roles),
        documentsApi.listForKbUpTo(kbId, CHECK_LIMIT),
      ]);
      const rows = await mapLimit(docs, 6, async (doc) => ({ doc, check: await documentsApi.accessCheck(doc.id, subject, groups, roles) }));
      return {
        principal: describePrincipal(subject, groups, roles),
        granted: kbCheck.unrestricted || kbCheck.knowledge_bases.some((k) => k.id === kbId),
        unrestricted: kbCheck.unrestricted,
        reason: kbCheck.reason,
        readable: rows.filter((r) => r.check.can_read),
        unreadable: rows.filter((r) => !r.check.can_read),
        truncated: docs.length >= CHECK_LIMIT,
      };
    },
  });

  // Grants and restrictions changed since this ran, so what it says is no longer true.
  useEffect(() => check.reset(), [resetKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const result = check.data;

  return (
    <section className="card p-5" aria-labelledby="access-check-title">
      <h2 id="access-check-title" className="font-display text-lg font-semibold">
        Check what a Principal can read
      </h2>
      <p className="mt-1 text-sm text-muted">
        Type who is asking, as their identity provider describes them, and see which Documents in this Knowledge Base they can and cannot read. It uses the same rules answers do, against the saved Grants and
        restrictions.
      </p>
      <form
        className="mt-4 space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (subject.trim()) check.mutate();
        }}
      >
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <div>
            <label className="label" htmlFor="check-subject">
              Subject
            </label>
            <input id="check-subject" className="input" value={subject} placeholder="e.g. alice@corp.example" onChange={(e) => setSubject(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="check-groups">
              Groups (optional)
            </label>
            <input id="check-groups" className="input" value={groups} placeholder="e.g. finance, platform" onChange={(e) => setGroups(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="check-roles">
              Roles (optional)
            </label>
            <input id="check-roles" className="input" value={roles} placeholder="e.g. hr-staff" onChange={(e) => setRoles(e.target.value)} />
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button type="submit" className="btn btn-primary" disabled={!subject.trim() || check.isPending}>
            <Icon icon={ShieldCheck} size="sm" />
            {check.isPending ? "Checking…" : "Check access"}
          </button>
          <span className="text-xs text-muted">Separate several groups or roles with commas.</span>
        </div>
      </form>

      <div className="mt-5" aria-live="polite">
        {check.isPending && <Skeleton className="h-24 w-full" />}
        {check.isError && (
          <p role="alert" className="text-sm text-danger">
            {(check.error as Error).message}
          </p>
        )}
        {result && <Outcome result={result} />}
      </div>
    </section>
  );
}

function Outcome({ result }: { result: CheckOutcome }) {
  return (
    <div className="space-y-4">
      <Callout
        tone={result.granted ? "ok" : "warn"}
        title={result.granted ? `${result.principal} can retrieve from this Knowledge Base` : `${result.principal} cannot retrieve from this Knowledge Base`}
      >
        {result.unrestricted ? `No Grants apply (${result.reason}).` : result.granted ? "A Grant covers them." : "No Grant covers them, so none of its Documents are readable."}
      </Callout>
      {result.truncated && <p className="text-sm text-muted">Only the first {CHECK_LIMIT} Documents were checked.</p>}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <DocumentList title="Can read" rows={result.readable} empty="No Documents." />
        <DocumentList title="Cannot read" rows={result.unreadable} empty="No Documents." showReason />
      </div>
    </div>
  );
}

function DocumentList({ title, rows, empty, showReason }: { title: string; rows: Row[]; empty: string; showReason?: boolean }) {
  return (
    <div className="rounded-panel border border-line">
      <h3 className="border-b border-line bg-sunken/50 px-4 py-2 text-sm font-medium">
        {title} <span className="text-muted">({rows.length})</span>
      </h3>
      {rows.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted">{empty}</p>
      ) : (
        <ul aria-label={title} className="divide-y divide-line">
          {rows.map(({ doc, check }) => (
            <li key={doc.id} className="px-4 py-2.5 text-sm">
              <div className="flex flex-wrap items-center gap-2 font-medium">
                {doc.title}
                {check.restricted && (
                  <Chip tone="clay" icon={Lock}>
                    Restricted
                  </Chip>
                )}
              </div>
              {showReason && <p className="mt-0.5 text-xs text-muted">{check.reason}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Grants({ kbId, onSaved }: { kbId: string; onSaved: () => void }) {
  const qc = useQueryClient();
  const grants = useQuery({ queryKey: ["kb-grants", kbId], queryFn: () => knowledgeBasesApi.grants(kbId) });
  const security = useQuery({ queryKey: ["system", "security"], queryFn: systemApi.security });
  const [draft, setDraft] = useState<KnowledgeBaseGrantEntry[] | null>(null);
  const saved = grants.data?.map(strip) ?? null;

  useEffect(() => {
    if (saved && draft === null) setDraft(saved);
  }, [saved, draft]);

  const save = useMutation({
    mutationFn: (entries: KnowledgeBaseGrantEntry[]) => knowledgeBasesApi.replaceGrants(kbId, entries),
    onSuccess: (result) => {
      setDraft(result.map(strip));
      qc.setQueryData(["kb-grants", kbId], result);
      onSaved();
    },
  });

  const dirty = !!draft && !!saved && !sameSet(draft, saved);
  const authOff = security.data?.auth_mode === "disabled";
  // Only when the platform says so: with authentication off, or ungranted access allowed, an empty list opens the door.
  const ungranted = !!saved && saved.length === 0 && deniesUngranted(security.data);

  return (
    <section className="card space-y-4 p-5" aria-labelledby="grants-title">
      <div>
        <h2 id="grants-title" className="font-display text-lg font-semibold">
          Grants
        </h2>
        <p className="mt-1 text-sm text-muted">
          A Grant lets a Principal read this Knowledge Base. Grants only ever add access: a caller needs to match just one. To narrow who sees a single Document, restrict it on the Documents tab.
        </p>
      </div>

      {authOff && (
        <Callout tone="warn" title="Authentication is off">
          Everyone can retrieve from every Knowledge Base. Grants saved here take effect once authentication is turned on.
        </Callout>
      )}
      {ungranted && (
        <Callout tone="warn" title="No one has been granted this Knowledge Base">
          With no Grants, signed-in people cannot retrieve from it, because this platform denies access to a Knowledge Base nobody has been granted.
          {security.data?.admin_bypass_retrieval ? " Admins can still read it." : ""} Add a Grant below.
        </Callout>
      )}
      {grants.isError && (
        <p role="alert" className="text-sm text-danger">
          {(grants.error as Error).message}
        </p>
      )}

      {grants.isLoading && <Skeleton className="h-10 w-full" />}
      {draft && <PrincipalEditor idPrefix="grant" entries={draft} onChange={setDraft} empty="No Grants." />}

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="btn btn-primary" disabled={!dirty || save.isPending} onClick={() => draft && save.mutate(draft)}>
          {save.isPending ? "Saving…" : "Save Grants"}
        </button>
        {dirty && (
          <button type="button" className="btn btn-ghost" onClick={() => setDraft(saved)}>
            Discard changes
          </button>
        )}
        {save.isSuccess && !dirty && <span className="text-sm text-ok">Saved</span>}
        {save.isError && (
          <span role="alert" className="text-sm text-danger">
            {(save.error as Error).message}
          </span>
        )}
      </div>
    </section>
  );
}
