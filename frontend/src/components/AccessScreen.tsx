"use client";
import { ReactNode } from "react";
import { SkeletonText, Wordmark } from "@/components/ui";
import { API_BASE } from "@/lib/api/client";

// Full-page screens for everything that happens before someone reaches an app: signing in, having
// no access, a sign-in that failed, an API that cannot be reached. They are set in the User UI's
// visual language whichever UI the person would land in, because they have not landed yet.

function Screen({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div data-surface="user" className="flex min-h-screen flex-col bg-canvas text-fg">
      <header className="px-6 py-5 sm:px-10">
        <Wordmark className="text-lg" />
      </header>
      <main id="main" tabIndex={-1} className="flex flex-1 items-center px-6 pb-24 outline-none sm:px-10">
        <div className="mx-auto w-full max-w-xl border-l-2 border-accent pl-6 sm:pl-8">
          <h1 className="font-display text-3xl font-semibold tracking-tight">{title}</h1>
          <div className="mt-4 space-y-3 text-base text-muted">{children}</div>
          {actions && <div className="mt-7 flex flex-wrap items-center gap-3">{actions}</div>}
        </div>
      </main>
    </div>
  );
}

export function CheckingScreen({ label = "Checking your session…" }: { label?: string }) {
  return (
    <Screen title={label}>
      <div role="status">
        <span className="sr-only">{label}</span>
        <SkeletonText lines={2} className="max-w-sm" />
      </div>
    </Screen>
  );
}

export function SignedOutScreen({ onSignIn }: { onSignIn?: () => void }) {
  if (onSignIn) {
    return (
      <Screen
        title="Sign in to continue"
        actions={
          <button className="btn btn-primary px-5 py-2.5" onClick={onSignIn}>
            Sign in with your company account
          </button>
        }
      >
        <p>Use your company account to open Enterprise Local RAG.</p>
      </Screen>
    );
  }
  return (
    <Screen
      title="Sign in to continue"
      actions={
        <button className="btn btn-primary px-5 py-2.5" onClick={() => window.location.reload()}>
          Reload
        </button>
      }
    >
      <p>Your session is missing or has expired. Reload the page to sign in again through your company login.</p>
      <p className="text-sm text-subtle">
        If this keeps happening, the API needs a sign-in this build is not set up for. An administrator can set
        NEXT_PUBLIC_AUTH_MODE when building the frontend.
      </p>
    </Screen>
  );
}

export function SignInFailedScreen({ detail, onRetry }: { detail: string; onRetry?: () => void }) {
  return (
    <Screen
      title="We couldn’t sign you in"
      actions={
        onRetry && (
          <button className="btn btn-primary px-5 py-2.5" onClick={onRetry}>
            Try again
          </button>
        )
      }
    >
      <p>The sign-in did not complete, so you have not been signed in. Trying again is safe.</p>
      <p className="rounded-control bg-sunken px-3 py-2 font-mono text-xs text-fg">{detail}</p>
    </Screen>
  );
}

const ROLES: [string, string][] = [
  ["querier", "ask questions in chat"],
  ["admin", "set up Applications and Knowledge Bases"],
  ["auditor", "read the audit log"],
];

export function NoAccessScreen({ subject, onSignOut }: { subject?: string; onSignOut?: () => void }) {
  return (
    <Screen
      title="Your account has no access"
      actions={
        onSignOut && (
          <button className="btn btn-secondary px-5 py-2.5" onClick={onSignOut}>
            Sign out
          </button>
        )
      }
    >
      <p>
        {subject ? (
          <>
            You are signed in as <span className="font-medium text-fg">{subject}</span>, but your account holds none of the roles this
            platform uses.
          </>
        ) : (
          "Your account holds none of the roles this platform uses."
        )}{" "}
        Ask an administrator to add one:
      </p>
      <ul className="space-y-1.5">
        {ROLES.map(([role, what]) => (
          <li key={role}>
            <span className="font-mono text-sm text-fg">{role}</span> to {what}
          </li>
        ))}
      </ul>
    </Screen>
  );
}

export function ApiUnreachableScreen({ onRetry }: { onRetry: () => void }) {
  return (
    <Screen
      title="Can’t reach the platform"
      actions={
        <button className="btn btn-primary px-5 py-2.5" onClick={onRetry}>
          Try again
        </button>
      }
    >
      <p>
        The page got no answer from the API at <span className="font-mono text-sm text-fg">{API_BASE}</span>. Check that the backend is
        running and that this address is the right one, then try again.
      </p>
    </Screen>
  );
}
