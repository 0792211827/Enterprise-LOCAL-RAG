"use client";
import { ArrowUp, Paperclip, Square, UploadCloud } from "lucide-react";
import { ChangeEvent, DragEvent, FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui";
import type { ChatAttachment } from "@/lib/api/types";
import { AttachmentChips } from "./AttachmentChips";
import { acceptedExtensions } from "./attachments";

const MAX_LENGTH = 4000;

/**
 * The box a question is written in, pinned to the bottom. Enter sends, Shift+Enter breaks the line; a file
 * can be attached with the button or dropped on the box.
 */
export function Composer({
  placeholder,
  disabled,
  busy,
  imagesEnabled,
  pending,
  uploading,
  errors,
  onSend,
  onStop,
  onAttach,
  onDetach,
}: {
  placeholder: string;
  disabled: boolean;
  busy: boolean;
  imagesEnabled: boolean;
  pending: ChatAttachment[];
  uploading: boolean;
  errors: string[];
  onSend: (text: string) => void;
  onStop: () => void;
  onAttach: (files: File[]) => void;
  onDetach: (attachment: ChatAttachment) => void;
}) {
  const [draft, setDraft] = useState("");
  const [dragging, setDragging] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const depth = useRef(0);

  // Grow with what is typed, up to a few lines, then scroll.
  useEffect(() => {
    const el = field.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 192)}px`;
  }, [draft]);

  const submit = () => {
    const text = draft.trim();
    if (!text || busy || disabled) return;
    onSend(text);
    setDraft("");
  };
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const picked = (e: ChangeEvent<HTMLInputElement>) => {
    onAttach(Array.from(e.target.files ?? []));
    e.target.value = "";
  };
  const carriesFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
  const canAttach = !disabled && !busy && !uploading;
  const onDrag = (e: DragEvent) => {
    if (!carriesFiles(e)) return;
    e.preventDefault();
    if (e.type === "dragenter") depth.current += 1;
    if (e.type === "dragenter" && canAttach) setDragging(true);
  };
  const onDragLeave = (e: DragEvent) => {
    if (!carriesFiles(e)) return;
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setDragging(false);
  };
  const onDrop = (e: DragEvent) => {
    if (!carriesFiles(e)) return;
    e.preventDefault();
    depth.current = 0;
    setDragging(false);
    if (canAttach) onAttach(Array.from(e.dataTransfer.files));
  };

  const searched = pending.filter((a) => a.searched);

  return (
    <div className="mx-auto w-full max-w-[42rem] shrink-0 px-4 pb-4 pt-2 sm:px-6">
      {errors.length > 0 && (
        <div role="alert" aria-label="Attachment problems" className="mb-2 rounded-control border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-fg">
          <ul className="space-y-1">
            {errors.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        </div>
      )}
      {searched.length > 0 && (
        <div className="mb-2 space-y-1 rounded-control border border-clay/30 bg-clay-soft px-3 py-2 text-sm text-fg">
          {searched.map((a) => (
            <p key={a.id}>
              &ldquo;{a.filename}&rdquo; is too long to send in full, so it will be searched for the passages that answer each
              question.
            </p>
          ))}
        </div>
      )}
      <form
        aria-label="Ask a question"
        onSubmit={onSubmit}
        onDragEnter={onDrag}
        onDragOver={onDrag}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={`relative rounded-panel border bg-surface shadow-raised transition-colors duration-fast focus-within:border-accent ${
          dragging ? "border-accent bg-accent-soft" : "border-line-strong"
        }`}
      >
        {dragging && (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center gap-2 rounded-panel bg-accent-soft/95 text-sm font-medium text-accent-text">
            <Icon icon={UploadCloud} />
            Drop to attach
          </div>
        )}
        {pending.length > 0 && (
          <div className="px-3 pt-3">
            <AttachmentChips attachments={pending} onRemove={onDetach} />
          </div>
        )}
        <textarea
          ref={field}
          rows={1}
          className="block max-h-48 w-full resize-none bg-transparent px-4 py-3.5 text-base text-fg outline-none placeholder:text-subtle disabled:opacity-60"
          placeholder={placeholder}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          maxLength={MAX_LENGTH}
          disabled={disabled}
          aria-label="Your question"
        />
        <div className="flex items-center justify-between gap-2 px-2 pb-2">
          <input
            ref={picker}
            type="file"
            multiple
            accept={acceptedExtensions(imagesEnabled)}
            className="hidden"
            onChange={picked}
            aria-label="Choose files to attach"
            tabIndex={-1}
          />
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => picker.current?.click()}
            disabled={!canAttach}
            aria-label="Attach a file"
          >
            <Icon icon={Paperclip} />
            <span className="hidden sm:inline">{uploading ? "Reading…" : "Attach"}</span>
          </button>
          {busy ? (
            <button type="button" className="btn btn-secondary" onClick={onStop} aria-label="Stop">
              <Icon icon={Square} size="sm" className="fill-current" />
              Stop
            </button>
          ) : (
            <button type="submit" className="btn btn-primary" disabled={!draft.trim() || disabled} aria-label="Send">
              <Icon icon={ArrowUp} />
            </button>
          )}
        </div>
      </form>
      <p className="mt-2 px-1 text-xs text-subtle">
        Attachments are private to this conversation: only you can see them, and they are deleted with it.
        <span className="hidden sm:inline"> Enter to send, Shift+Enter for a new line.</span>
      </p>
    </div>
  );
}
