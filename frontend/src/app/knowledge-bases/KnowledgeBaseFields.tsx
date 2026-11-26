"use client";
export interface KnowledgeBaseForm {
  name: string;
  description: string;
}

export const INITIAL_KB_FORM: KnowledgeBaseForm = { name: "", description: "" };

/** The fields a Knowledge Base is created and edited with. */
export function KnowledgeBaseFields({
  value,
  onChange,
  autoFocus = false,
}: {
  value: KnowledgeBaseForm;
  onChange: (patch: Partial<KnowledgeBaseForm>) => void;
  autoFocus?: boolean;
}) {
  return (
    <>
      <div>
        <label className="label" htmlFor="kb-name">
          Name
        </label>
        <input id="kb-name" className="input" required autoFocus={autoFocus} value={value.name} onChange={(e) => onChange({ name: e.target.value })} placeholder="HR handbook" />
      </div>
      <div>
        <label className="label" htmlFor="kb-description">
          Description (optional)
        </label>
        <textarea id="kb-description" className="input" rows={2} value={value.description} onChange={(e) => onChange({ description: e.target.value })} />
      </div>
    </>
  );
}
