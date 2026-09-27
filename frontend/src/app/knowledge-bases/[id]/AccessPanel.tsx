"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Callout } from "@/components/ui";
import { knowledgeBasesApi, systemApi } from "@/lib/api/resources";
import type { GrantPrincipalType, KnowledgeBaseGrantEntry } from "@/lib/api/types";

const TYPE_LABELS: Record<GrantPrincipalType, string> = {
  group: "Group",
  role: "Role",
  subject: "User (subject)",
  everyone: "Everyone",
};

const PLACEHOLDERS: Record<GrantPrincipalType, string> = {
  group: "IdP group, e.g. finance-analysts",
  role: "IdP role value, e.g. hr-staff",
  subject: "IdP subject, e.g. alice@corp.example",
  everyone: "Any authenticated querier",
};

// Who may retrieve from this knowledge base. Grants name IdP claim values, and
// saving replaces the whole set, so what is shown is exactly what is enforced.
export function AccessPanel({ kbId }: { kbId: string }) {
  const qc = useQueryClient();
  const grants = useQuery({ queryKey: ["kb-grants", kbId], queryFn: () => knowledgeBasesApi.grants(kbId) });
  const security = useQuery({ queryKey: ["system", "security"], queryFn: systemApi.security });
  const [draft, setDraft] = useState<KnowledgeBaseGrantEntry[] | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (grants.data && draft === null) {
      setDraft(grants.data.map(({ principal_type, principal_value }) => ({ principal_type, principal_value })));
    }
  }, [grants.data, draft]);

  const save = useMutation({
    mutationFn: (entries: KnowledgeBaseGrantEntry[]) => knowledgeBasesApi.replaceGrants(kbId, entries),
    onSuccess: (result) => {
      setDraft(result.map(({ principal_type, principal_value }) => ({ principal_type, principal_value })));
      setSaved(true);
      qc.invalidateQueries({ queryKey: ["kb-grants", kbId] });
    },
  });

  const update = (index: number, patch: Partial<KnowledgeBaseGrantEntry>) => {
    setSaved(false);
    setDraft((rows) => (rows ?? []).map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };
  const invalid = (draft ?? []).some((g) => g.principal_type !== "everyone" && !g.principal_value.trim());
  const authOff = security.data?.auth_mode === "disabled";

  return (
    <section className="card mt-6">
      <div className="border-b border-slate-100 px-5 py-4 text-sm font-semibold text-slate-700">Access</div>
      <div className="space-y-4 p-5">
        {authOff ? (
          <Callout tone="warn">
            Authentication is off (<code>AUTH__MODE=disabled</code>), so everyone can retrieve from every knowledge
            base. Grants saved here take effect once authentication is turned on.
          </Callout>
        ) : (
          <Callout tone="info">
            Only callers matching a grant below can retrieve from this knowledge base
            {security.data ? "" : " once authentication is on"}. Values are matched against IdP token claims.
          </Callout>
        )}

        {grants.isError && <p className="text-sm text-red-600">{(grants.error as Error).message}</p>}
        {draft && draft.length === 0 && (
          <p className="text-sm text-slate-500">No grants: with authentication on, nobody can retrieve from this knowledge base.</p>
        )}

        {draft?.map((grant, index) => (
          <div key={index} className="flex items-center gap-2">
            {/* Widths live on wrappers: .input applies w-full, which would win. */}
            <div className="w-44 shrink-0">
              <select
                className="input"
                value={grant.principal_type}
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
                value={grant.principal_type === "everyone" ? "" : grant.principal_value}
                disabled={grant.principal_type === "everyone"}
                placeholder={PLACEHOLDERS[grant.principal_type]}
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

        <div className="flex items-center gap-2">
          <button
            className="btn btn-secondary"
            onClick={() => {
              setSaved(false);
              setDraft((rows) => [...(rows ?? []), { principal_type: "group", principal_value: "" }]);
            }}
          >
            + Add grant
          </button>
          <button
            className="btn btn-primary"
            disabled={!draft || invalid || save.isPending}
            onClick={() => draft && save.mutate(draft)}
          >
            {save.isPending ? "Saving…" : "Save access"}
          </button>
          {saved && <span className="text-sm text-emerald-600">Saved</span>}
          {save.isError && <span className="text-sm text-red-600">{(save.error as Error).message}</span>}
        </div>

        <AccessCheck kbId={kbId} />
      </div>
    </section>
  );
}

// "Would this identity see this knowledge base?" -- evaluated by the same
// resolver the answer endpoints use.
function AccessCheck({ kbId }: { kbId: string }) {
  const [subject, setSubject] = useState("");
  const [groups, setGroups] = useState("");
  const [roles, setRoles] = useState("");
  const check = useMutation({ mutationFn: () => knowledgeBasesApi.accessCheck(subject, groups, roles) });
  const allowed = check.data && (check.data.unrestricted || check.data.knowledge_bases.some((kb) => kb.id === kbId));

  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <div className="mb-2 text-sm font-medium text-slate-700">Check access</div>
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
          <span className={`text-sm ${allowed ? "text-emerald-600" : "text-amber-700"}`}>
            {allowed ? "Can retrieve from this knowledge base" : "Cannot retrieve from this knowledge base"}
            {check.data.unrestricted && ` (${check.data.reason})`}
          </span>
        )}
        {check.isError && <span className="text-sm text-red-600">{(check.error as Error).message}</span>}
      </div>
      <p className="mt-2 text-xs text-slate-400">Reflects saved grants, not unsaved edits above.</p>
    </div>
  );
}
