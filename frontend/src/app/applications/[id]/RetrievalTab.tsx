"use client";
import Link from "next/link";
import type { Application } from "@/lib/api/types";

const MODES: Record<string, string> = { hybrid: "Hybrid (keyword and meaning)", bm25: "Keyword only", vector: "Meaning only" };

export function RetrievalTab({ app }: { app: Application }) {
  const r = app.retrieval_configuration;
  const gen = app.model_configuration?.generation_params ?? {};
  const rows: [string, string | number][] = r
    ? [
        ["Retrieval mode", MODES[r.mode] ?? r.mode],
        ["Passages used (top K)", r.top_k],
        ["Score threshold", r.score_threshold ?? "None"],
        ["Hybrid candidate multiplier", r.hybrid_size_multiplier],
        ["Rank fusion constant", r.rrf_rank_constant],
        ["Temperature", String(gen.temperature ?? "—")],
        ["Maximum answer length", String(gen.max_tokens ?? "—")],
      ]
    : [];

  return (
    <div className="max-w-2xl space-y-4">
      <p className="text-sm text-muted">
        How passages are found before the model writes an answer. These are fixed when the Application is created. To see how the platform&apos;s shared profiles are set up, open{" "}
        <Link href="/retrieval" className="text-accent-text underline">
          Retrieval
        </Link>
        .
      </p>
      {rows.length === 0 ? (
        <p className="rounded-panel border border-dashed border-line-strong px-4 py-5 text-sm text-muted">This Application has no retrieval profile recorded.</p>
      ) : (
        <dl className="card divide-y divide-line">
          {rows.map(([label, value]) => (
            <div key={label} className="flex justify-between gap-4 px-5 py-3 text-sm">
              <dt className="text-muted">{label}</dt>
              <dd className="font-medium text-fg">{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
