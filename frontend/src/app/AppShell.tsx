"use client";
import { useQuery } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect } from "react";
import {
  ApiUnreachableScreen,
  CheckingScreen,
  NoAccessScreen,
  SignedOutScreen,
} from "@/components/AccessScreen";
import { AdminShell } from "@/components/AdminShell";
import { OidcSession } from "@/components/OidcSession";
import { UserShell } from "@/components/UserShell";
import { ApiError } from "@/lib/api/client";
import { authApi } from "@/lib/api/resources";
import { AUTH_MODE } from "@/lib/auth";
import { signIn, signOut } from "@/lib/oidc";
import { homeFor, roleForPath, surfaceForPath } from "@/lib/surfaces";

// Decides what a caller sees, from who the API says they are:
//   not signed in           -> the sign-in screen
//   API not answering       -> a screen that says so, with a retry
//   signed in, no role      -> the no-access screen
//   wrong surface for them  -> sent to the one they have (End users to chat, Admins to the Dashboard,
//                              auditors to the Audit Log)
//   otherwise               -> the User shell for chat, the Admin shell for everything else
// The API enforces the same split; this keeps the UI from offering pages that would only answer 403.
function Gate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const oidc = AUTH_MODE === "oidc";
  const me = useQuery({ queryKey: ["auth", "me"], queryFn: authApi.me, retry: false });

  const roles = me.data?.roles;
  const needed = roleForPath(pathname);
  const allowed = !roles || needed === null || roles.includes(needed);
  const home = roles ? homeFor(roles) : null;

  useEffect(() => {
    if (!allowed && home) router.replace(home);
  }, [allowed, home, router]);

  // The OIDC callback page is handled by OidcSession; nothing here applies to it.
  if (needed === null) return <>{children}</>;

  if (me.isLoading) return <CheckingScreen />;
  if (me.isError) {
    if (me.error instanceof ApiError && me.error.status === 401) {
      return <SignedOutScreen onSignIn={oidc ? () => signIn() : undefined} />;
    }
    return <ApiUnreachableScreen onRetry={() => me.refetch()} />;
  }
  if (!roles) return <CheckingScreen />;
  if (!home) return <NoAccessScreen subject={me.data?.subject} onSignOut={oidc ? () => signOut() : undefined} />;
  if (!allowed) return <CheckingScreen label="Taking you to your page…" />;

  const shellProps = {
    roles,
    onSignIn: oidc ? () => signIn() : undefined,
    onSignOut: oidc ? () => signOut() : undefined,
  };
  const surface = surfaceForPath(pathname);
  if (surface === "bare") return <div className="mx-auto max-w-7xl px-6 py-8">{children}</div>;
  if (surface === "user") return <UserShell {...shellProps}>{children}</UserShell>;
  return <AdminShell {...shellProps}>{children}</AdminShell>;
}

export function AppShell({ children }: { children: ReactNode }) {
  const gate = <Gate>{children}</Gate>;
  return AUTH_MODE === "oidc" ? <OidcSession>{gate}</OidcSession> : gate;
}
