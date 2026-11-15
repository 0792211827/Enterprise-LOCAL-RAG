"use client";
import { FileText, Image as ImageIcon, X } from "lucide-react";
import { Icon } from "@/components/ui";
import type { ChatAttachment } from "@/lib/api/types";
import { IMAGE_KINDS, formatSize } from "./attachments";

/** Attached files as chips: name, type and size, and a way to take one off. */
export function AttachmentChips({
  attachments,
  onRemove,
}: {
  attachments: ChatAttachment[];
  onRemove?: (attachment: ChatAttachment) => void;
}) {
  if (attachments.length === 0) return null;
  return (
    <ul aria-label="Attachments" className="flex flex-wrap gap-2">
      {attachments.map((a) => (
        <li
          key={a.id}
          className="inline-flex max-w-full items-center gap-2 rounded-control border border-line-strong bg-surface py-1.5 pl-2.5 pr-2.5 text-xs text-fg"
          title={a.searched ? `${a.filename} is too long to send in full, so it is searched for each question` : a.filename}
        >
          <Icon icon={IMAGE_KINDS.includes(a.kind) ? ImageIcon : FileText} size="sm" className="text-subtle" />
          <span className="min-w-0 max-w-[14rem] truncate font-medium">{a.filename}</span>
          <span className="shrink-0 text-subtle">
            {a.kind.toUpperCase()} · {formatSize(a.size_bytes)}
          </span>
          {a.searched && <span className="shrink-0 rounded-sm bg-clay-soft px-1.5 py-0.5 font-medium text-clay-text">Searched</span>}
          {onRemove && (
            <button
              type="button"
              className="-mr-1 shrink-0 rounded-sm p-0.5 text-subtle hover:bg-sunken hover:text-fg"
              onClick={() => onRemove(a)}
              aria-label={`Remove ${a.filename}`}
            >
              <Icon icon={X} size="sm" />
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
