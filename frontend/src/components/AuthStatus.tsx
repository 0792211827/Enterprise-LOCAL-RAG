"use client";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { AUTH_MODE, onAuthRequired } from "@/lib/auth";
import { authApi } from "@/lib/api/resources";

// Who the API thinks you are, from /auth/me. Shown at the foot of the sidebar
// so an operator can see at a glance which roles their session carries.
export function CallerBadge({ onSignOut }: { onSignOut?: () => void }) {
  const me = useQuery({ queryKey: ["auth", "me"], queryFn: authApi.me, retry: false });

  if (me.isLoading) return null;
  if (!me.data) {
    return <div className="text-xs text-slate-400">Not signed in</div>;
  }
  if (me.data.auth_method === "disabled") {
    return (
      <div className="text-xs text-slate-400" title="Backend AUTH__MODE=disabled">
        Authentication off
      </div>
    );
  }
  return (
    <div className="text-xs">
      <div className="truncate font-medium text-slate-700" title={me.data.subject}>
        {me.data.subject}
      </div>
      <div className="mt-1 flex flex-wrap gap-1">
        {me.data.roles.map((role) => (
          <span key={role} className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600">
            {role}
          </span>
        ))}
      </div>
      {onSignOut && (
        <button className="mt-2 text-slate-500 underline hover:text-slate-700" onClick={onSignOut}>
          Sign out
        </button>
      )}
    </div>
  );
}

// Appears when any API call returns 401: the session is missing or expired.
export function AuthBanner({ onSignIn }: { onSignIn?: () => void }) {
  const [required, setRequired] = useState(false);
  useEffect(() => onAuthRequired(() => setRequired(true)), []);
  if (!required) return null;

  const action =
    AUTH_MODE === "oidc" && onSignIn ? (
      <button className="btn btn-secondary" onClick={onSignIn}>
        Sign in again
      </button>
    ) : (
      <button className="btn btn-secondary" onClick={() => window.location.reload()}>
        Reload
      </button>
    );

  return (
    <div className="fixed inset-x-0 top-0 z-50 ml-64 flex items-center justify-between gap-4 border-b border-amber-200 bg-amber-50 px-8 py-3 text-sm text-amber-800">
      <span>
        {AUTH_MODE === "none"
          ? "The API requires authentication, but this UI was built with NEXT_PUBLIC_AUTH_MODE=none."
          : "Your session is missing or has expired."}
      </span>
      {action}
    </div>
  );
}
