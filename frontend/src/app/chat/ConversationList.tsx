"use client";
import { Check, Pencil, Plus, Trash2 } from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { Icon, Modal } from "@/components/ui";
import type { ConversationSummary } from "@/lib/api/types";

const GROUPS = ["Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older"] as const;
type Group = (typeof GROUPS)[number];

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

function groupOf(iso: string, now: Date): Group {
  const days = Math.round((startOfDay(now) - startOfDay(new Date(iso))) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days <= 7) return "Previous 7 days";
  if (days <= 30) return "Previous 30 days";
  return "Older";
}

/** The End user's past Conversations, newest first and grouped by how recent they are. */
export function ConversationList({
  conversations,
  failed,
  activeId,
  onNew,
  onOpen,
  onRename,
  onDelete,
  onRetry,
}: {
  conversations: ConversationSummary[];
  failed: boolean;
  activeId: string | null;
  onNew: () => void;
  onOpen: (conversation: ConversationSummary) => void;
  onRename: (conversation: ConversationSummary, title: string) => void;
  onDelete: (conversation: ConversationSummary) => void;
  onRetry: () => void;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<ConversationSummary | null>(null);

  const grouped = useMemo(() => {
    const now = new Date();
    const sorted = [...conversations].sort((a, b) => b.last_activity_at.localeCompare(a.last_activity_at));
    return GROUPS.map((name) => ({ name, items: sorted.filter((c) => groupOf(c.last_activity_at, now) === name) })).filter(
      (g) => g.items.length > 0,
    );
  }, [conversations]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="p-3">
        <button type="button" className="btn btn-secondary w-full justify-start" onClick={onNew}>
          <Icon icon={Plus} />
          New chat
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {failed && (
          <p className="px-2 py-4 text-sm text-muted">
            Your conversations couldn&apos;t be loaded.{" "}
            <button type="button" className="underline underline-offset-2" onClick={onRetry}>
              Try again
            </button>
          </p>
        )}
        {!failed && conversations.length === 0 && (
          <p className="px-2 py-6 text-center text-sm text-subtle">Your conversations will appear here.</p>
        )}
        {grouped.map(({ name, items }) => (
          <section key={name} className="mb-3">
            <h3 className="px-2 pb-1 pt-2 text-xs font-medium text-subtle">{name}</h3>
            <ul>
              {items.map((c) =>
                renaming === c.id ? (
                  <li key={c.id}>
                    <RenameField
                      title={c.title}
                      onSave={(title) => {
                        setRenaming(null);
                        if (title && title !== c.title) onRename(c, title);
                      }}
                      onCancel={() => setRenaming(null)}
                    />
                  </li>
                ) : (
                  <li
                    key={c.id}
                    className={`group flex items-center rounded-control ${c.id === activeId ? "bg-accent-soft" : "hover:bg-sunken"}`}
                  >
                    <button
                      type="button"
                      className={`min-w-0 flex-1 truncate rounded-control px-2.5 py-2 text-left text-sm ${
                        c.id === activeId ? "font-medium text-accent-text" : "text-fg"
                      }`}
                      onClick={() => onOpen(c)}
                      aria-current={c.id === activeId ? "true" : undefined}
                      title={c.title}
                    >
                      {c.title || "Untitled"}
                    </button>
                    <span className="flex shrink-0 pr-1 opacity-0 transition-opacity duration-fast focus-within:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100">
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm !px-1.5"
                        onClick={() => setRenaming(c.id)}
                        aria-label={`Rename ${c.title || "conversation"}`}
                      >
                        <Icon icon={Pencil} size="sm" />
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm !px-1.5 hover:!text-danger"
                        onClick={() => setDeleting(c)}
                        aria-label={`Delete ${c.title || "conversation"}`}
                      >
                        <Icon icon={Trash2} size="sm" />
                      </button>
                    </span>
                  </li>
                ),
              )}
            </ul>
          </section>
        ))}
      </div>
      <p className="border-t border-line p-3 text-xs leading-relaxed text-subtle">
        Only you can read your conversations. They are deleted automatically after a retention period.
      </p>

      <Modal open={!!deleting} onClose={() => setDeleting(null)} title="Delete this conversation?">
        <p className="text-sm text-fg">
          &ldquo;{deleting?.title || "Untitled"}&rdquo; and its messages and attachments will be removed for good. This can&apos;t be
          undone.
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" className="btn btn-secondary" onClick={() => setDeleting(null)}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary !bg-danger hover:!bg-danger/90"
            onClick={() => {
              if (deleting) onDelete(deleting);
              setDeleting(null);
            }}
          >
            Delete
          </button>
        </div>
      </Modal>
    </div>
  );
}

function RenameField({ title, onSave, onCancel }: { title: string; onSave: (title: string) => void; onCancel: () => void }) {
  const [value, setValue] = useState(title);
  const input = useRef<HTMLInputElement>(null);
  const done = useRef(false);
  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);

  const save = () => {
    if (done.current) return;
    done.current = true;
    onSave(value.trim());
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save();
  };
  const keys = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      done.current = true;
      onCancel();
    }
  };
  return (
    <form onSubmit={submit} className="flex items-center gap-1 px-1 py-1">
      <input
        ref={input}
        className="input !py-1.5"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={keys}
        onBlur={save}
        maxLength={200}
        aria-label="Conversation name"
      />
      <button type="submit" className="btn btn-ghost btn-sm !px-1.5" aria-label="Save name" onMouseDown={(e) => e.preventDefault()}>
        <Icon icon={Check} size="sm" />
      </button>
    </form>
  );
}
