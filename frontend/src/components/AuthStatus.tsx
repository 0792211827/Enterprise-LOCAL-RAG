"use client";
import { useQuery } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { useEffect, useState } from "react";
import { Chip, Icon } from "@/components/ui";
import { authApi } from "@/lib/api/resources";
import { AUTH_MODE, onAuthRequired } from "@/lib/auth";

/** Who the API thinks you are, from /auth/me, with sign-out when the app signs people in itself. */
export function CallerBadge({
  onSignOut,
  collapsible = false,
  showRoles = true,
}: {
  onSignOut?: () => void;
  collapsible?: boolean;
  showRoles?: boolean;
}) {
  const me = useQuery({ queryKey: ["auth", "me"], queryFn: authApi.me, retry: false });
  if (!me.data) return null;

  const off = me.data.auth_method === "disabled";
  const label = off ? "Authentication off" : me.data.subject;
  // In a collapsed sidebar only an initial and the sign-out icon fit; the text returns at wide widths.
  const wide = collapsible ? "hidden lg:block" : "";
  return (
    <div className="flex items-center gap-2 text-xs">
      {collapsible && (
        <span
          title={label}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent-text lg:hidden"
        >
          {off ? "–" : label.charAt(0).toUpperCase()}
        </span>
      )}
      <div className={`min-w-0 flex-1 ${wide}`}>
        <div className="truncate font-medium text-fg" title={label}>
          {label}
        </div>
        {!off && showRoles && (
          <div className="mt-1 flex flex-wrap gap-1">
            {me.data.roles.map((role) => (
              <Chip key={role}>{role}</Chip>
            ))}
          </div>
        )}
      </div>
      {onSignOut && (
        <button type="button" className="btn btn-ghost btn-sm shrink-0" onClick={onSignOut} aria-label="Sign out" title="Sign out">
          <Icon icon={LogOut} size="sm" />
          <span className={collapsible ? "hidden lg:inline" : ""}>Sign out</span>
        </button>
      )}
    </div>
  );
}

// Appears when an API call returns 401 after the page loaded: the session expired mid-use.
export function AuthBanner({ onSignIn }: { onSignIn?: () => void }) {
  const [required, setRequired] = useState(false);
  useEffect(() => onAuthRequired(() => setRequired(true)), []);
  if (!required) return null;

  const action =
    AUTH_MODE === "oidc" && onSignIn ? (
      <button className="btn btn-secondary btn-sm" onClick={onSignIn}>
        Sign in again
      </button>
    ) : (
      <button className="btn btn-secondary btn-sm" onClick={() => window.location.reload()}>
        Reload
      </button>
    );

  return (
    <div role="alert" className="mb-4 flex items-center justify-between gap-4 rounded-panel border border-clay/30 bg-clay-soft px-4 py-3 text-sm text-fg">
      <span>
        {AUTH_MODE === "none"
          ? "The API requires authentication, but this UI was built with NEXT_PUBLIC_AUTH_MODE=none."
          : "Your session is missing or has expired."}
      </span>
      {action}
    </div>
  );
}
