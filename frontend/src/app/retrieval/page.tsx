"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, SlidersHorizontal, Trash2 } from "lucide-react";
import { useState } from "react";
import {
  Advanced,
  ConfirmModal,
  EmptyState,
  ErrorState,
  Icon,
  Modal,
  PageHeader,
  TBody,
  Table,
  TableSkeleton,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui";
import { retrievalApi } from "@/lib/api/resources";
import type { RetrievalConfig, RetrievalMode } from "@/lib/api/types";

const MODES: Record<string, string> = { hybrid: "Hybrid", bm25: "Keyword only", vector: "Meaning only" };
const MODE_HINTS: Record<RetrievalMode, string> = {
  hybrid: "Finds passages by keyword and by meaning, then merges the two rankings. The best default.",
  bm25: "Matches the words in the question. Good for names, codes and exact terms.",
  vector: "Matches the meaning of the question, even when the words differ.",
};

export default function RetrievalPage() {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<RetrievalConfig | null>(null);
  const list = useQuery({ queryKey: ["retrieval"], queryFn: retrievalApi.list });

  const remove = useMutation({
    mutationFn: (id: string) => retrievalApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["retrieval"] });
      setDeleting(null);
    },
  });

  const create = (
    <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
      <Icon icon={Plus} size="sm" />
      New retrieval profile
    </button>
  );

  return (
    <div>
      <PageHeader
        title="Retrieval profiles"
        description="Named ways of finding passages (keyword, meaning or both, and how many to use), kept in one place so Applications can share them."
        action={create}
      />

      {list.isLoading && <TableSkeleton columns={6} label="Loading retrieval profiles" />}
      {list.isError && <ErrorState title="Could not load retrieval profiles" message={(list.error as Error).message} onRetry={() => list.refetch()} />}
      {list.data && list.data.length === 0 && (
        <EmptyState
          icon={SlidersHorizontal}
          title="No retrieval profiles yet"
          description="Create a profile to reuse the same search settings across Applications."
          action={create}
        />
      )}
      {list.data && list.data.length > 0 && (
        <Table caption="Retrieval profiles">
          <THead>
            <tr>
              <Th>Profile</Th>
              <Th>Mode</Th>
              <Th align="right">Passages (top K)</Th>
              <Th align="right">Candidate multiplier</Th>
              <Th align="right">Fusion constant</Th>
              <Th align="right">Minimum score</Th>
              <Th />
            </tr>
          </THead>
          <TBody>
            {list.data.map((c) => {
              const hybrid = c.mode === "hybrid";
              return (
                <Tr key={c.id}>
                  <Td>
                    <span className="font-medium">{c.name}</span>
                  </Td>
                  <Td>{MODES[c.mode] ?? c.mode}</Td>
                  <Td align="right">{c.top_k}</Td>
                  <Td align="right">{hybrid ? c.hybrid_size_multiplier : "—"}</Td>
                  <Td align="right">{hybrid ? c.rrf_rank_constant : "—"}</Td>
                  <Td align="right">{c.score_threshold ?? "—"}</Td>
                  <Td align="right">
                    <button type="button" className="btn btn-ghost btn-sm" aria-label={`Delete ${c.name}`} onClick={() => setDeleting(c)}>
                      <Icon icon={Trash2} size="sm" />
                    </button>
                  </Td>
                </Tr>
              );
            })}
          </TBody>
        </Table>
      )}

      {creating && <ProfileForm onClose={() => setCreating(false)} />}

      <ConfirmModal
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title={deleting ? `Delete ${deleting.name}?` : "Delete retrieval profile"}
        confirmLabel="Delete profile"
        pending={remove.isPending}
        error={remove.isError ? (remove.error as Error).message : null}
        onConfirm={() => deleting && remove.mutate(deleting.id)}
      >
        <p>
          <strong className="text-fg">{deleting?.name}</strong> will be deleted. Any Application that uses it is left with no retrieval profile.
        </p>
      </ConfirmModal>
    </div>
  );
}

interface Form {
  name: string;
  mode: RetrievalMode;
  top_k: string;
  score_threshold: string;
  hybrid_size_multiplier: string;
  rrf_rank_constant: string;
}

const INITIAL: Form = { name: "", mode: "hybrid", top_k: "8", score_threshold: "", hybrid_size_multiplier: "2", rrf_rank_constant: "60" };

// Bounds the API enforces, checked here so a bad value is explained before it is sent.
const RANGES = {
  top_k: [1, 100],
  hybrid_size_multiplier: [1, 10],
  rrf_rank_constant: [1, 1000],
} as const;

const outOfRange = (value: string, [min, max]: readonly [number, number]) => {
  const n = Number(value);
  return value.trim() === "" || !Number.isInteger(n) || n < min || n > max;
};

function ProfileForm({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<Form>(INITIAL);
  const patch = (p: Partial<Form>) => setForm((f) => ({ ...f, ...p }));
  const hybrid = form.mode === "hybrid";

  const errors = {
    top_k: outOfRange(form.top_k, RANGES.top_k),
    hybrid_size_multiplier: hybrid && outOfRange(form.hybrid_size_multiplier, RANGES.hybrid_size_multiplier),
    rrf_rank_constant: hybrid && outOfRange(form.rrf_rank_constant, RANGES.rrf_rank_constant),
    score_threshold: form.score_threshold.trim() !== "" && Number.isNaN(Number(form.score_threshold)),
  };
  const valid = form.name.trim() !== "" && !Object.values(errors).some(Boolean);

  const save = useMutation({
    mutationFn: () =>
      retrievalApi.create({
        name: form.name.trim(),
        mode: form.mode,
        top_k: Number(form.top_k),
        ...(form.score_threshold.trim() !== "" && { score_threshold: Number(form.score_threshold) }),
        ...(hybrid && { hybrid_size_multiplier: Number(form.hybrid_size_multiplier), rrf_rank_constant: Number(form.rrf_rank_constant) }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["retrieval"] });
      onClose();
    },
  });

  const number = (id: string, label: string, key: keyof typeof RANGES | "score_threshold", help: string, placeholder?: string) => (
    <div>
      <label className="label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        type="number"
        className="input"
        value={form[key]}
        placeholder={placeholder}
        aria-invalid={errors[key]}
        aria-describedby={`${id}-help`}
        onChange={(e) => patch({ [key]: e.target.value })}
      />
      <p id={`${id}-help`} className={`mt-1.5 text-xs ${errors[key] ? "text-danger" : "text-muted"}`}>
        {help}
      </p>
    </div>
  );

  return (
    <Modal open onClose={onClose} title="New retrieval profile" size="lg">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) save.mutate();
        }}
      >
        <div>
          <label className="label" htmlFor="profile-name">
            Name
          </label>
          <input id="profile-name" className="input" autoFocus value={form.name} onChange={(e) => patch({ name: e.target.value })} placeholder="Careful policy search" />
        </div>
        <div>
          <label className="label" htmlFor="profile-mode">
            How passages are found
          </label>
          <select id="profile-mode" className="input" value={form.mode} onChange={(e) => patch({ mode: e.target.value as RetrievalMode })}>
            {(Object.keys(MODES) as RetrievalMode[]).map((m) => (
              <option key={m} value={m}>
                {MODES[m]}
              </option>
            ))}
          </select>
          <p className="mt-1.5 text-xs text-muted">{MODE_HINTS[form.mode]}</p>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {number("profile-top-k", "Passages to use (top K)", "top_k", "A whole number from 1 to 100. More gives the model more to read.")}
          {number("profile-score", "Minimum score (optional)", "score_threshold", "Passages scoring below this are dropped. Leave empty to keep them all.", "None")}
        </div>
        {hybrid && (
          <Advanced title="Tuning the merge">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {number("profile-multiplier", "Candidate multiplier", "hybrid_size_multiplier", "From 1 to 10. How many more candidates each search finds before they are merged.")}
              {number("profile-rrf", "Fusion constant", "rrf_rank_constant", "From 1 to 1000. Higher values give lower-ranked passages more weight.")}
            </div>
          </Advanced>
        )}
        {save.isError && (
          <p role="alert" className="text-sm text-danger">
            {(save.error as Error).message}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={!valid || save.isPending}>
            {save.isPending ? "Saving…" : "Create profile"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
