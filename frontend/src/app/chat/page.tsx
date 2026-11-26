"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessagesSquare, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Drawer, EmptyState, ErrorState, Icon, LoadingState, useToast } from "@/components/ui";
import { chatApi } from "@/lib/api/resources";
import type { ChatCitation, ConversationSummary } from "@/lib/api/types";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { ApplicationSwitcher } from "./ApplicationSwitcher";
import { CitationPanel } from "./CitationPanel";
import { Composer } from "./Composer";
import { ConversationList } from "./ConversationList";
import { MessageStream } from "./MessageStream";
import { useConversation } from "./useConversation";

const LAST_APPLICATION = "chat.lastApplication";
const readLastApplication = () => {
  try {
    return window.localStorage.getItem(LAST_APPLICATION);
  } catch {
    return null;
  }
};
const rememberApplication = (id: string) => {
  try {
    window.localStorage.setItem(LAST_APPLICATION, id);
  } catch {
    /* a browser that will not remember is no reason to stop chatting */
  }
};

interface CitedAnswer {
  citations: ChatCitation[];
  selected: number;
}

const Centred = ({ children }: { children: React.ReactNode }) => (
  <div className="mx-auto w-full max-w-[42rem] px-6 py-10">{children}</div>
);

/** The End user's chat: Conversations on the left, the reading column in the middle, a Citation on the right. */
export default function ChatPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const apps = useQuery({ queryKey: ["chat", "applications"], queryFn: chatApi.applications });
  const history = useQuery({ queryKey: ["chat", "conversations"], queryFn: chatApi.conversations });
  // The image control is offered only when the operator has switched image upload on.
  const config = useQuery({ queryKey: ["chat", "config"], queryFn: chatApi.config, staleTime: 5 * 60_000 });
  const railInline = useMediaQuery("(min-width: 768px)");
  const panelBeside = useMediaQuery("(min-width: 1100px)");

  const [appId, setAppId] = useState("");
  const [railOpen, setRailOpen] = useState(false);
  const [cited, setCited] = useState<CitedAnswer | null>(null);
  const [opener, setOpener] = useState<HTMLElement | null>(null);

  const refreshHistory = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ["chat", "conversations"] }),
    [queryClient],
  );
  const application = apps.data?.find((a) => a.id === appId);
  const chat = useConversation({ application, config: config.data, onHistoryChanged: refreshHistory });
  const { startNew } = chat;

  // Start from the Application used last, or the first offered.
  useEffect(() => {
    if (!apps.data || appId) return;
    const last = readLastApplication();
    setAppId(apps.data.find((a) => a.id === last)?.id ?? apps.data[0]?.id ?? "");
  }, [apps.data, appId]);

  // A source belongs to the Conversation it was opened in.
  useEffect(() => {
    setCited(null);
  }, [chat.view]);

  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => chatApi.renameConversation(id, title),
    onSuccess: refreshHistory,
    onError: (err: Error) => toast({ tone: "danger", title: "Couldn't rename the conversation", description: err.message }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => chatApi.deleteConversation(id),
    onSuccess: (_, id) => {
      if (id === chat.conversationId) startNew();
      refreshHistory();
    },
    onError: (err: Error) => toast({ tone: "danger", title: "Couldn't delete the conversation", description: err.message }),
  });

  const choose = (id: string) => {
    if (id === appId) return;
    startNew();
    setAppId(id);
    rememberApplication(id);
  };

  const openConversation = async (summary: ConversationSummary) => {
    setRailOpen(false);
    const owner = await chat.open(summary);
    if (owner) {
      setAppId(owner);
      // An Application the End user can no longer use is shown as such, not swapped for another.
      if (apps.data?.some((a) => a.id === owner)) rememberApplication(owner);
    }
  };

  const newChat = () => {
    setRailOpen(false);
    startNew();
  };

  const openSource = useCallback((citations: ChatCitation[], index: number, from: HTMLElement) => {
    setOpener(from);
    setCited({ citations, selected: Math.max(0, citations.findIndex((c) => c.index === index)) });
  }, []);
  const closeSource = useCallback(() => {
    setCited(null);
    // The panel beside the answer does not hand focus back by itself, as a sheet does.
    if (opener?.isConnected) requestAnimationFrame(() => opener.focus());
  }, [opener]);
  const selectSource = useCallback((position: number) => setCited((c) => c && { ...c, selected: position }), []);

  if (apps.isLoading || history.isLoading) {
    return (
      <Centred>
        <LoadingState label="Opening chat…" />
      </Centred>
    );
  }
  if (apps.isError) {
    return (
      <Centred>
        <ErrorState message={(apps.error as Error).message} onRetry={() => apps.refetch()} />
      </Centred>
    );
  }
  if (!apps.data?.length) {
    return (
      <Centred>
        <EmptyState
          icon={MessagesSquare}
          title="Nothing has been made available to you yet"
          description="You can chat once you have been given access to something to ask. Ask an administrator of this platform to grant it to you."
        />
      </Centred>
    );
  }

  const conversations = (
    <ConversationList
      conversations={history.data ?? []}
      failed={history.isError}
      activeId={chat.conversationId}
      onNew={newChat}
      onOpen={openConversation}
      onRename={(c, title) => rename.mutate({ id: c.id, title })}
      onDelete={(c) => remove.mutate(c.id)}
      onRetry={() => history.refetch()}
    />
  );

  return (
    <div className="flex min-h-0 flex-1">
      {railInline ? (
        <aside aria-label="Conversation history" className="w-64 shrink-0 border-r border-line bg-canvas">
          {conversations}
        </aside>
      ) : (
        <Drawer open={railOpen} onClose={() => setRailOpen(false)} title="Conversations" side="left" flush>
          {conversations}
        </Drawer>
      )}

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-line px-4 py-3 sm:px-6">
          <div className="flex min-w-0 items-start gap-3">
            {!railInline && (
              <button
                type="button"
                className="btn btn-secondary btn-sm mt-0.5 shrink-0"
                onClick={() => setRailOpen(true)}
                aria-label="Conversations"
              >
                <Icon icon={MessagesSquare} size="sm" />
                <span className="hidden sm:inline">Conversations</span>
              </button>
            )}
            <ApplicationSwitcher applications={apps.data} selected={application} onSelect={choose} />
          </div>
          {!railInline && chat.turns.length > 0 && (
            <button type="button" className="btn btn-secondary btn-sm mt-0.5 shrink-0" onClick={newChat} aria-label="New chat">
              <Icon icon={Plus} size="sm" />
              <span className="hidden sm:inline">New chat</span>
            </button>
          )}
        </header>

        {chat.openError && (
          <p role="alert" className="border-b border-danger/30 bg-danger-soft px-4 py-2 text-sm text-fg sm:px-6">
            {chat.openError}
          </p>
        )}
        {chat.conversationId && !application && (
          <p className="border-b border-clay/30 bg-clay-soft px-4 py-2 text-sm text-fg sm:px-6">
            You can read this conversation, but the application it was with is no longer available to you.
          </p>
        )}

        <div className="flex min-h-0 flex-1">
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <MessageStream
              turns={chat.turns}
              conversationId={chat.conversationId}
              busy={chat.busy}
              application={application}
              resetKey={chat.view}
              onCite={openSource}
              onRetry={chat.retry}
              onDetach={chat.detach}
            />
            <Composer
              placeholder={application ? `Ask ${application.name} a question…` : "Ask a question…"}
              disabled={!application}
              busy={chat.busy}
              imagesEnabled={config.data?.images_enabled === true}
              pending={chat.pending}
              uploading={chat.uploading}
              errors={chat.attachErrors}
              onSend={chat.send}
              onStop={chat.stop}
              onAttach={chat.attach}
              onDetach={chat.detach}
            />
          </div>
          {cited && (
            <CitationPanel
              citations={cited.citations}
              selected={cited.selected}
              sheet={!panelBeside}
              onSelect={selectSource}
              onClose={closeSource}
            />
          )}
        </div>
      </div>
    </div>
  );
}
