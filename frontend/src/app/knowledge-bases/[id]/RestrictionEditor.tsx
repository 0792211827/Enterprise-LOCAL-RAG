"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Callout } from "@/components/ui";
import { documentsApi } from "@/lib/api/resources";
import type { GrantPrincipalType, KnowledgeBaseGrantEntry } from "@/lib/api/types";

const TYPE_LABELS: Record<GrantPrincipalType, string> = {
  group: "Group",
  role: "Role",
  subject: "User (subject)",
  everyone: "Everyone",
};

const PLACEHOLDERS: Record<GrantPrincipalType, string> = {
  group: "IdP group, e.g. hr-leads",
  role: "IdP role value, e.g. hr-director",
  subject: "IdP subject, e.g. alice@corp.example",
  everyone: "Any caller who holds the knowledge base grant",
};

const strip = ({ principal_type, principal_value }: KnowledgeBaseGrantEntry) => ({ principal_type, principal_value });

// Restricts one Document to chosen principals. Restrictions only narrow: a caller still needs a grant on the
// knowledge base, and an unrestricted Document inherits the grants. Saving replaces the whole set, and the
// indexed chunks are updated in place, so there is nothing to re-ingest.
export function RestrictionEditor({ kbId, documentId }: { kbId: string; documentId: string }) {
  const qc = useQueryClient();
  const current = useQuery({ queryKey: ["doc-restrictions", documentId], queryFn: () => documentsApi.restrictions(documentId) });
  const [draft, setDraft] = useState<KnowledgeBaseGrantEntry[] | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (current.data && draft === null) setDraft(current.data.restrictions.map(strip));
  }, [current.data, draft]);

  const save = useMutation({
    mutationFn: (entries: KnowledgeBaseGrantEntry[]) => documentsApi.replaceRestrictions(documentId, entries),
    onSuccess: (result) => {
      setDraft(result.restrictions.map(strip));
      setSaved(true);
      qc.setQueryData(["doc-restrictions", documentId], result);
      qc.invalidateQueries({ queryKey: ["doc-restriction-map", kbId] });
    },
  });

  const update = (index: number, patch: Partial<KnowledgeBaseGrantEntry>) => {
    setSaved(false);
    setDraft((rows) => (rows ?? []).map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };
  const invalid = (draft ?? []).some((r) => r.principal_type !== "everyone" && !r.principal_value.trim());
  const warnings = save.data?.warnings ?? current.data?.warnings ?? [];

  return (
    <div className="space-y-3">
      {current.isError && <p className="text-sm text-red-600">{(current.error as Error).message}</p>}
      <p className="text-xs text-slate-500">
        {draft && draft.length === 0
          ? "Unrestricted: anyone with a grant on this knowledge base can read this document."
          : "Only callers with a grant on this knowledge base who match one of these can read this document."}
      </p>

      {draft?.map((row, index) => (
        <div key={index} className="flex items-center gap-2">
          <div className="w-44 shrink-0">
            <select
              className="input"
              value={row.principal_type}
              onChange={(e) => update(index, { principal_type: e.target.value as GrantPrincipalType })}
            >
              {(Object.keys(TYPE_LABELS) as GrantPrincipalType[]).map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABELS[t]}
                </option>
              ))}
            </select>
          </div>
          <div className="min-w-0 flex-1">
            <input
              className="input"
              value={row.principal_type === "everyone" ? "" : row.principal_value}
              disabled={row.principal_type === "everyone"}
              placeholder={PLACEHOLDERS[row.principal_type]}
              onChange={(e) => update(index, { principal_value: e.target.value })}
            />
          </div>
          <button
            className="btn btn-secondary"
            onClick={() => {
              setSaved(false);
              setDraft((rows) => (rows ?? []).filter((_, i) => i !== index));
            }}
          >
            Remove
          </button>
        </div>
      ))}

      {warnings.map((w, i) => (
        <Callout key={i} tone="warn">
          {w.message}
        </Callout>
      ))}

      <div className="flex items-center gap-2">
        <button
          className="btn btn-secondary"
          onClick={() => {
            setSaved(false);
            setDraft((rows) => [...(rows ?? []), { principal_type: "group", principal_value: "" }]);
          }}
        >
          + Add principal
        </button>
        <button
          className="btn btn-primary"
          disabled={!draft || invalid || save.isPending}
          onClick={() => draft && save.mutate(draft)}
        >
          {save.isPending ? "Saving…" : draft && draft.length === 0 ? "Save (clears restriction)" : "Save restriction"}
        </button>
        {saved && (
          <span className="text-sm text-emerald-600">
            Saved{save.data?.chunks_updated != null ? ` — ${save.data.chunks_updated} chunks updated` : ""}
          </span>
        )}
        {save.isError && <span className="text-sm text-red-600">{(save.error as Error).message}</span>}
      </div>

      <DocumentAccessCheck documentId={documentId} />
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
    <div className="rounded-lg border border-slate-200 p-3">
      <div className="mb-2 text-sm font-medium text-slate-700">Check access to this document</div>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <input className="input" placeholder="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} />
        <input className="input" placeholder="Groups (comma-separated)" value={groups} onChange={(e) => setGroups(e.target.value)} />
        <input className="input" placeholder="Roles (comma-separated)" value={roles} onChange={(e) => setRoles(e.target.value)} />
      </div>
      <div className="mt-2 flex items-center gap-3">
        <button className="btn btn-secondary" disabled={!subject.trim() || check.isPending} onClick={() => check.mutate()}>
          Check
        </button>
        {check.data && (
          <span className={`text-sm ${check.data.can_read ? "text-emerald-600" : "text-amber-700"}`}>
            {check.data.can_read ? "Can read" : "Cannot read"} — {check.data.reason}
          </span>
        )}
        {check.isError && <span className="text-sm text-red-600">{(check.error as Error).message}</span>}
      </div>
      <p className="mt-2 text-xs text-slate-400">Reflects saved grants and restrictions, not unsaved edits above.</p>
    </div>
  );
}
