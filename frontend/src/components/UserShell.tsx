"use client";
import Link from "next/link";
import { ReactNode } from "react";
import { AuthBanner, CallerBadge } from "@/components/AuthStatus";
import { Wordmark } from "@/components/ui";
import type { Role } from "@/lib/surfaces";

// The User UI: calm and editorial, warm paper. A slim header and the page; the chat itself
// (Application switcher, Conversation rail) is built inside it.
export function UserShell({
  roles,
  onSignIn,
  onSignOut,
  children,
}: {
  roles: Role[];
  onSignIn?: () => void;
  onSignOut?: () => void;
  children: ReactNode;
}) {
  return (
    <div data-surface="user" className="min-h-screen bg-canvas text-fg">
      <header className="flex h-12 items-center justify-between gap-4 border-b border-line px-4 sm:px-6">
        <Link href="/chat" aria-label="Enterprise Local RAG chat">
          <Wordmark className="text-base" />
        </Link>
        <div className="flex items-center gap-4">
          {roles.includes("admin") && (
            <Link href="/" className="text-sm text-muted hover:text-fg hover:underline">
              Admin
            </Link>
          )}
          <CallerBadge onSignOut={onSignOut} showRoles={false} />
        </div>
      </header>
      <main className="px-4 py-2 sm:px-6">
        <AuthBanner onSignIn={onSignIn} />
        {children}
      </main>
    </div>
  );
}
