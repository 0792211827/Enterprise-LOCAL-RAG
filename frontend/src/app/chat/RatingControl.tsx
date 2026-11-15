"use client";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { Icon } from "@/components/ui";
import { chatApi } from "@/lib/api/resources";
import type { MessageFeedback } from "@/lib/api/types";

/** Thumbs, an optional comment, and the explicit choice to share this one exchange with admins. */
export function RatingControl({
  conversationId,
  messageId,
  feedback,
  onRated,
}: {
  conversationId: string;
  messageId: string;
  feedback: MessageFeedback | null;
  onRated: (feedback: MessageFeedback | null) => void;
}) {
  const [editing, setEditing] = useState<"up" | "down" | null>(null);
  const [comment, setComment] = useState(feedback?.comment ?? "");
  const [share, setShare] = useState(feedback?.share_with_admins ?? false);
  const [error, setError] = useState<string | null>(null);

  const begin = (rating: "up" | "down") => {
    setComment(feedback?.comment ?? "");
    setShare(feedback?.share_with_admins ?? false);
    setError(null);
    setEditing(rating);
  };
  const save = async () => {
    if (!editing) return;
    try {
      const saved = await chatApi.rate(conversationId, messageId, {
        rating: editing,
        comment: comment.trim() || null,
        share_with_admins: share,
      });
      onRated(saved);
      setEditing(null);
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const withdraw = async () => {
    try {
      await chatApi.withdrawRating(conversationId, messageId);
      onRated(null);
      setEditing(null);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const thumb = (rating: "up" | "down", label: string, glyph: typeof ThumbsUp) => (
    <button
      type="button"
      onClick={() => begin(rating)}
      aria-label={label}
      aria-pressed={feedback?.rating === rating}
      className={`btn btn-ghost btn-sm ${feedback?.rating === rating ? "!bg-accent-soft !text-accent-text" : ""}`}
    >
      <Icon icon={glyph} size="sm" />
    </button>
  );

  return (
    <div>
      <div className="flex items-center gap-1 text-xs text-subtle">
        {thumb("up", "Good answer", ThumbsUp)}
        {thumb("down", "Bad answer", ThumbsDown)}
        {feedback?.share_with_admins && <span className="ml-1">Shared with admins</span>}
      </div>
      {editing && (
        <div className="mt-2 max-w-md space-y-2 font-sans">
          <textarea
            className="input h-16 w-full"
            placeholder="Add a comment (optional): admins will see it"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            maxLength={2000}
            aria-label="Comment"
          />
          <label className="flex items-start gap-2 text-xs text-muted">
            <input type="checkbox" className="mt-0.5" checked={share} onChange={(e) => setShare(e.target.checked)} />
            <span>
              Share this exchange with admins. They will see this question and answer, and nothing else from your
              conversations.
            </span>
          </label>
          {error && <div className="text-xs text-danger">{error}</div>}
          <div className="flex gap-2">
            <button type="button" className="btn btn-primary btn-sm" onClick={save}>
              Save rating
            </button>
            {feedback && (
              <button type="button" className="btn btn-secondary btn-sm" onClick={withdraw}>
                Remove rating
              </button>
            )}
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
