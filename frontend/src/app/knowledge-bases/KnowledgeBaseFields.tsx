"use client";
import type { RetrievalMode } from "@/lib/api/types";

export interface KnowledgeBaseForm {
  name: string;
  description: string;
  retrieval_mode: RetrievalMode;
  default_top_k: number;
}

export const INITIAL_KB_FORM: KnowledgeBaseForm = { name: "", description: "", retrieval_mode: "hybrid", default_top_k: 8 };

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
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="kb-mode">
            Retrieval mode
          </label>
          <select id="kb-mode" className="input" value={value.retrieval_mode} onChange={(e) => onChange({ retrieval_mode: e.target.value as RetrievalMode })}>
            <option value="hybrid">Hybrid (keyword and meaning)</option>
            <option value="bm25">Keyword only</option>
            <option value="vector">Meaning only</option>
          </select>
        </div>
        <div>
          <label className="label" htmlFor="kb-topk">
            Default passages (top K)
          </label>
          <input id="kb-topk" type="number" min={1} max={100} className="input" value={value.default_top_k} onChange={(e) => onChange({ default_top_k: Number(e.target.value) })} />
        </div>
      </div>
    </>
  );
}
