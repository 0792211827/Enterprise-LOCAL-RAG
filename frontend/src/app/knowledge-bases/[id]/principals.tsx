"use client";
import { Globe, Plus, User, UserCheck, Users } from "lucide-react";
import { useState } from "react";
import { Chip, Icon, type IconComponent } from "@/components/ui";
import type { GrantPrincipalType, KnowledgeBaseGrantEntry } from "@/lib/api/types";

export const TYPE_LABELS: Record<GrantPrincipalType, string> = {
  group: "Group",
  role: "Role",
  subject: "Subject",
  everyone: "Everyone",
};

const TYPE_ICONS: Record<GrantPrincipalType, IconComponent> = {
  group: Users,
  role: UserCheck,
  subject: User,
  everyone: Globe,
};

const PLACEHOLDERS: Record<GrantPrincipalType, string> = {
  group: "IdP group, e.g. finance-analysts",
  role: "IdP role, e.g. hr-staff",
  subject: "IdP subject, e.g. alice@corp.example",
  everyone: "Any authenticated caller",
};

export const strip = ({ principal_type, principal_value }: KnowledgeBaseGrantEntry): KnowledgeBaseGrantEntry => ({
  principal_type,
  principal_value,
});

export const sameEntry = (a: KnowledgeBaseGrantEntry, b: KnowledgeBaseGrantEntry) =>
  a.principal_type === b.principal_type && (a.principal_type === "everyone" || a.principal_value === b.principal_value);

export const sameSet = (a: KnowledgeBaseGrantEntry[], b: KnowledgeBaseGrantEntry[]) =>
  a.length === b.length && a.every((x) => b.some((y) => sameEntry(x, y)));

export const principalText = (e: KnowledgeBaseGrantEntry) =>
  e.principal_type === "everyone" ? "Everyone" : `${TYPE_LABELS[e.principal_type]} · ${e.principal_value}`;

/** A Principal as a typed chip: the icon and the type word say which kind it is, so colour is not the only cue. */
export function PrincipalChip({
  entry,
  onRemove,
  tone = "accent",
}: {
  entry: KnowledgeBaseGrantEntry;
  onRemove?: () => void;
  tone?: "accent" | "clay" | "neutral";
}) {
  const text = principalText(entry);
  return (
    <Chip tone={tone} icon={TYPE_ICONS[entry.principal_type]} onRemove={onRemove} removeLabel={`Remove ${text}`}>
      {text}
    </Chip>
  );
}

export function PrincipalChips({ entries, tone }: { entries: KnowledgeBaseGrantEntry[]; tone?: "accent" | "clay" | "neutral" }) {
  return (
    <ul className="flex flex-wrap gap-1.5">
      {entries.map((e) => (
        <li key={`${e.principal_type}:${e.principal_value}`}>
          <PrincipalChip entry={e} tone={tone} />
        </li>
      ))}
    </ul>
  );
}

/** Principals as removable chips, with a row to add one. Edits a draft: saving is the caller's business. */
export function PrincipalEditor({
  entries,
  onChange,
  idPrefix,
  tone,
  empty,
}: {
  entries: KnowledgeBaseGrantEntry[];
  onChange: (entries: KnowledgeBaseGrantEntry[]) => void;
  idPrefix: string;
  tone?: "accent" | "clay" | "neutral";
  empty: string;
}) {
  const [type, setType] = useState<GrantPrincipalType>("group");
  const [value, setValue] = useState("");
  const everyone = type === "everyone";
  const candidate: KnowledgeBaseGrantEntry = { principal_type: type, principal_value: everyone ? "" : value.trim() };
  const duplicate = entries.some((e) => sameEntry(e, candidate));
  const canAdd = (everyone || candidate.principal_value !== "") && !duplicate;

  const add = () => {
    if (!canAdd) return;
    onChange([...entries, candidate]);
    setValue("");
  };

  return (
    <div className="space-y-3">
      {entries.length === 0 ? (
        <p className="text-sm text-muted">{empty}</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {entries.map((e) => (
            <li key={`${e.principal_type}:${e.principal_value}`}>
              <PrincipalChip entry={e} tone={tone} onRemove={() => onChange(entries.filter((x) => !sameEntry(x, e)))} />
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-end gap-2">
        {/* Widths live on wrappers: .input applies w-full, which would win. */}
        <div className="w-36 shrink-0">
          <label className="label" htmlFor={`${idPrefix}-type`}>
            Type
          </label>
          <select id={`${idPrefix}-type`} className="input" value={type} onChange={(e) => setType(e.target.value as GrantPrincipalType)}>
            {(Object.keys(TYPE_LABELS) as GrantPrincipalType[]).map((t) => (
              <option key={t} value={t}>
                {TYPE_LABELS[t]}
              </option>
            ))}
          </select>
        </div>
        <div className="min-w-[14rem] flex-1">
          <label className="label" htmlFor={`${idPrefix}-value`}>
            Value
          </label>
          <input
            id={`${idPrefix}-value`}
            className="input"
            value={everyone ? "" : value}
            disabled={everyone}
            placeholder={PLACEHOLDERS[type]}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
          />
        </div>
        <button type="button" className="btn btn-secondary" disabled={!canAdd} onClick={add}>
          <Icon icon={Plus} size="sm" />
          Add
        </button>
      </div>
      {duplicate && <p className="text-xs text-muted">That Principal is already in the list.</p>}
    </div>
  );
}
