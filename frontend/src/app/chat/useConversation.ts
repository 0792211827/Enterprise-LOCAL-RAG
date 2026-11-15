"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { chatApi } from "@/lib/api/resources";
import type {
  ChatApplication,
  ChatAttachment,
  ChatCitation,
  ChatConfig,
  ConversationSummary,
  MessageFeedback,
} from "@/lib/api/types";
import { checkFile, uploadError } from "./attachments";

export interface Turn {
  id?: string; // the saved message's id; known once an answer is complete
  role: "user" | "assistant";
  text: string;
  citations: ChatCitation[];
  feedback?: MessageFeedback | null;
  attachments?: ChatAttachment[];
  error?: string;
  streaming?: boolean;
  stopped?: boolean;
}

/**
 * One open Conversation: its messages, the answer being streamed into it, and the Attachments waiting to go
 * with the next question. The stream events are handled as they always were; this only gathers them in one
 * place so the screen can stay about layout.
 */
export function useConversation({
  application,
  config,
  onHistoryChanged,
}: {
  application: ChatApplication | undefined;
  config: Partial<ChatConfig> | undefined;
  onHistoryChanged: () => void;
}) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);
  const [pending, setPending] = useState<ChatAttachment[]>([]);
  const [attachErrors, setAttachErrors] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  // Changes when another Conversation is put on screen, not when the open one is first saved.
  const [view, setView] = useState(0);

  const abort = useRef<AbortController | null>(null);
  const currentId = useRef<string | null>(null);
  const pendingCount = useRef(0);
  // Bumped whenever the screen moves to another Conversation, so a stream that outlives its
  // Conversation cannot write into the next one.
  const generation = useRef(0);

  useEffect(() => {
    currentId.current = conversationId;
  }, [conversationId]);
  useEffect(() => {
    pendingCount.current = pending.length;
  }, [pending]);
  useEffect(() => () => abort.current?.abort(), []);

  const reset = useCallback(() => {
    generation.current += 1;
    abort.current?.abort();
    setBusy(false);
    setOpenError(null);
    setPending([]);
    setAttachErrors([]);
  }, []);

  const startNew = useCallback(() => {
    reset();
    currentId.current = null;
    setConversationId(null);
    setTurns([]);
    setView((v) => v + 1);
  }, [reset]);

  /** Opens a saved Conversation; resolves to its Application's id, or null if it could not be opened. */
  const open = useCallback(
    async (summary: ConversationSummary): Promise<string | null> => {
      reset();
      const mine = generation.current;
      try {
        const detail = await chatApi.conversation(summary.id);
        if (generation.current !== mine) return null;
        setConversationId(detail.id);
        setView((v) => v + 1);
        setTurns(
          detail.messages.map((m) => ({
            id: m.id,
            role: m.role,
            text: m.content,
            citations: m.citations,
            feedback: m.feedback,
            attachments: m.attachments,
          })),
        );
        setPending(detail.pending_attachments);
        return detail.application_id;
      } catch (err) {
        if (generation.current === mine) setOpenError((err as Error).message);
        return null;
      }
    },
    [reset],
  );

  const ask = useCallback(
    async (query: string, sending: ChatAttachment[]) => {
      if (!application) return;
      const mine = ++generation.current;
      const patchLast = (change: (turn: Turn) => Turn) => {
        if (generation.current !== mine) return;
        setTurns((all) => all.map((turn, i) => (i === all.length - 1 ? change(turn) : turn)));
      };
      setPending([]);
      setAttachErrors([]);
      setOpenError(null);
      setBusy(true);
      setTurns((all) => [
        ...all,
        { role: "user", text: query, citations: [], attachments: sending },
        { role: "assistant", text: "", citations: [], streaming: true },
      ]);
      const controller = new AbortController();
      abort.current = controller;
      try {
        let id = currentId.current;
        if (!id) {
          id = (await chatApi.startConversation(application.id)).id;
          if (generation.current !== mine) return;
          currentId.current = id;
          setConversationId(id);
        }
        await chatApi.ask(
          id,
          query,
          (event) => {
            if (event.type === "delta") patchLast((t) => ({ ...t, text: t.text + event.content }));
            if (event.type === "citations") patchLast((t) => ({ ...t, citations: event.citations }));
            if (event.type === "error") patchLast((t) => ({ ...t, error: event.message }));
            if (event.type === "done") patchLast((t) => ({ ...t, id: event.message_id ?? undefined }));
          },
          controller.signal,
        );
      } catch (err) {
        if ((err as Error).name === "AbortError") {
          patchLast((t) => ({ ...t, stopped: true }));
        } else {
          const message =
            err instanceof ApiError && err.status === 404
              ? `${application.name} is no longer available to you.`
              : (err as Error).message;
          patchLast((t) => ({ ...t, error: message }));
        }
      } finally {
        if (generation.current === mine) {
          patchLast((t) => ({ ...t, streaming: false }));
          setBusy(false);
          onHistoryChanged();
          // Whatever the server did not send with this message is still waiting to be sent.
          const id = currentId.current;
          if (id) {
            chatApi
              .attachments(id)
              .then((all) => generation.current === mine && setPending(all.filter((a) => !a.attached_to)))
              .catch(() => {});
          }
        }
      }
    },
    [application, onHistoryChanged],
  );

  const send = useCallback(
    (query: string) => {
      const text = query.trim();
      if (!text || busy) return;
      return ask(text, pending);
    },
    [ask, busy, pending],
  );

  const stop = useCallback(() => abort.current?.abort(), []);

  /** Asks the last question again, in place of the answer that failed. */
  const retry = useCallback(() => {
    if (busy || turns.length < 2) return;
    const question = turns[turns.length - 2];
    if (question.role !== "user") return;
    setTurns((all) => all.slice(0, -2));
    return ask(question.text, pending);
  }, [ask, busy, pending, turns]);

  const attach = useCallback(
    async (files: File[]) => {
      if (!files.length || !application || uploading) return;
      setAttachErrors([]);
      setUploading(true);
      const mine = generation.current;
      const errors: string[] = [];
      let count = pendingCount.current;
      try {
        for (const file of files) {
          const problem = checkFile(file, config, count);
          if (problem) {
            errors.push(problem);
            continue;
          }
          try {
            let id = currentId.current;
            if (!id) {
              id = (await chatApi.startConversation(application.id)).id;
              if (generation.current !== mine) return;
              currentId.current = id;
              setConversationId(id);
              onHistoryChanged();
            }
            const added = await chatApi.attach(id, file);
            if (generation.current !== mine) return;
            setPending((all) => [...all, added]);
            count += 1;
          } catch (err) {
            errors.push(uploadError(file, err));
          }
        }
      } finally {
        if (generation.current === mine) setAttachErrors(errors);
        setUploading(false);
      }
    },
    [application, config, onHistoryChanged, uploading],
  );

  const detach = useCallback(async (attachment: ChatAttachment) => {
    const id = currentId.current;
    if (!id) return;
    setAttachErrors([]);
    try {
      await chatApi.removeAttachment(id, attachment.id);
      setPending((all) => all.filter((a) => a.id !== attachment.id));
      setTurns((all) =>
        all.map((t) => (t.attachments ? { ...t, attachments: t.attachments.filter((a) => a.id !== attachment.id) } : t)),
      );
    } catch (err) {
      setAttachErrors([(err as Error).message]);
    }
  }, []);

  const setFeedback = useCallback((messageId: string, feedback: MessageFeedback | null) => {
    setTurns((all) => all.map((t) => (t.id === messageId ? { ...t, feedback } : t)));
  }, []);

  return {
    view,
    conversationId,
    turns,
    busy,
    openError,
    pending,
    attachErrors,
    uploading,
    startNew,
    open,
    send,
    stop,
    retry,
    attach,
    detach,
    setFeedback,
  };
}
