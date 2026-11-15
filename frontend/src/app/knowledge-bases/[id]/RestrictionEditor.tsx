"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Callout } from "@/components/ui";
import { documentsApi, knowledgeBasesApi, systemApi } from "@/lib/api/resources";
import type { KnowledgeBaseGrantEntry } from "@/lib/api/types";
import { deniesUngranted } from "@/lib/security";
import { PrincipalChips, PrincipalEditor, principalText, sameEntry, sameSet, strip } from "./principals";

// Restricts one Document to chosen Principals. Restrictions only narrow: a caller still needs a Grant on the
// Knowledge Base, and an unrestricted Document inherits the Grants. Saving replaces the whole set, and the
// indexed chunks are updated in place, so there is nothing to re-ingest.
export function RestrictionEditor({ kbId, documentId }: { kbId: string; documentId: string }) {
  const qc = useQueryClient();
  const current = useQuery({ queryKey: ["doc-restrictions", documentId], queryFn: () => documentsApi.restrictions(documentId) });
  const [draft, setDraft] = useState<KnowledgeBaseGrantEntry[] | null>(null);
  const saved = current.data?.restrictions.map(strip) ?? null;

  useEffect(() => {
    if (saved && draft === null) setDraft(saved);
  }, [saved, draft]);

  const save = useMutation({
    mutationFn: (entries: KnowledgeBaseGrantEntry[]) => documentsApi.replaceRestrictions(documentId, entries),
    onSuccess: (result) => {
      setDraft(result.restrictions.map(strip));
      qc.setQueryData(["doc-restrictions", documentId], result);
      qc.invalidateQueries({ queryKey: ["doc-restriction-map", kbId] });
    },
  });

  const dirty = !!draft && !!saved && !sameSet(draft, saved);
  const warnings = save.data?.warnings ?? current.data?.warnings ?? [];

  return (
    <div className="space-y-4">
      {current.isError && (
        <p role="alert" className="text-sm text-danger">
          {(current.error as Error).message}
        </p>
      )}
      <p className="text-sm text-muted">
        Restrictions only narrow access. A caller still needs a Grant on this Knowledge Base, and must also match one of the Principals below. With none, this Document inherits the Grants.
      </p>

      {draft && <PrincipalEditor idPrefix={`restriction-${documentId}`} tone="clay" entries={draft} onChange={setDraft} empty="No restriction." />}
      {draft && <EffectiveAccess kbId={kbId} restrictions={draft} />}

      {warnings.map((w, i) => (
        <Callout key={i} tone="warn">
          {w.message}
        </Callout>
      ))}

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="btn btn-primary" disabled={!dirty || save.isPending} onClick={() => draft && save.mutate(draft)}>
          {save.isPending ? "Saving…" : draft && draft.length === 0 ? "Clear restriction" : "Save restriction"}
        </button>
        {dirty && (
          <button type="button" className="btn btn-ghost" onClick={() => setDraft(saved)}>
            Discard changes
          </button>
        )}
        {save.isSuccess && !dirty && (
          <span className="text-sm text-ok">Saved{save.data?.chunks_updated != null ? `. ${save.data.chunks_updated} indexed passages updated.` : ""}</span>
        )}
        {save.isError && (
          <span role="alert" className="text-sm text-danger">
            {(save.error as Error).message}
          </span>
        )}
      </div>

      <DocumentAccessCheck documentId={documentId} />
    </div>
  );
}

/** What the draft restriction means once it is set against the Knowledge Base's Grants. */
function EffectiveAccess({ kbId, restrictions }: { kbId: string; restrictions: KnowledgeBaseGrantEntry[] }) {
  const grants = useQuery({ queryKey: ["kb-grants", kbId], queryFn: () => knowledgeBasesApi.grants(kbId) });
  const security = useQuery({ queryKey: ["system", "security"], queryFn: systemApi.security });
  if (!grants.data) return null;

  const held = grants.data.map(strip);
  const everyoneGranted = held.some((g) => g.principal_type === "everyone");
  const ungranted = held.length === 0 && deniesUngranted(security.data);

  return (
    <div className="space-y-3 rounded-control border border-line bg-sunken/40 p-4 text-sm" aria-label="Effective access">
      <div className="font-medium text-fg">Effective access</div>
      {ungranted ? (
        <p className="text-muted">The Knowledge Base has no Grants, so signed-in people cannot read this Document yet, whatever is set here.</p>
      ) : restrictions.length === 0 ? (
        <>
          <p className="text-muted">Unrestricted: this Document inherits the Grants, so it is readable by anyone who holds one.</p>
          {held.length > 0 && <PrincipalChips entries={held} />}
        </>
      ) : (
        <>
          <p className="text-muted">Only callers who hold a Grant and also match a restriction can read this Document.</p>
          <ul className="space-y-1.5">
            {restrictions.map((r) => {
              const covered = everyoneGranted || held.some((g) => sameEntry(g, r));
              return (
                <li key={`${r.principal_type}:${r.principal_value}`} className="flex flex-wrap items-center gap-2">
                  <PrincipalChips entries={[r]} tone="clay" />
                  <span className="text-xs text-muted">
                    {covered ? "Holds a Grant, so everyone it names can read." : `Not itself granted: it reaches only members who also match a Grant (${held.length ? held.map(principalText).join("; ") : "none"}).`}
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}

// "Could this identity read this document?" -- the same rule the answer paths apply.
function DocumentAccessCheck({ documentId }: { documentId: string }) {
  const [subject, setSubject] = useState("");
  const [groups, setGroups] = useState("");
  const [roles, setRoles] = useState("");
  const check = useMutation({ mutationFn: () => documentsApi.accessCheck(documentId, subject, groups, roles) });

  return (
    <form
      className="space-y-2 border-t border-line pt-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (subject.trim()) check.mutate();
      }}
    >
      <div className="text-sm font-medium text-fg">Check this Document alone</div>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <input className="input" aria-label="Subject" placeholder="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
        <input className="input" aria-label="Groups" placeholder="Groups (comma-separated)" value={groups} onChange={(e) => setGroups(e.target.value)} />
        <input className="input" aria-label="Roles" placeholder="Roles (comma-separated)" value={roles} onChange={(e) => setRoles(e.target.value)} />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn btn-secondary" disabled={!subject.trim() || check.isPending}>
          Check
        </button>
        {check.data && (
          <span className={`text-sm ${check.data.can_read ? "text-ok" : "text-clay-text"}`}>
            {check.data.can_read ? "Can read" : "Cannot read"}. {check.data.reason}
          </span>
        )}
        {check.isError && (
          <span role="alert" className="text-sm text-danger">
            {(check.error as Error).message}
          </span>
        )}
      </div>
      <p className="text-xs text-muted">Reflects the saved Grants and restrictions, not unsaved edits above.</p>
    </form>
  );
}
