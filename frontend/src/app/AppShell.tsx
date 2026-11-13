"use client";
import { useQuery } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { AuthBanner } from "@/components/AuthStatus";
import { OidcSession } from "@/components/OidcSession";
import { Sidebar } from "@/components/Sidebar";
import { EmptyState, LoadingState } from "@/components/ui";
import { authApi } from "@/lib/api/resources";
import { AUTH_MODE } from "@/lib/auth";
import { signIn, signOut } from "@/lib/oidc";
import { homeFor, roleForPath } from "@/lib/surfaces";

// Keeps a signed-in user on the surface their roles reach: employees get chat, not the control plane.
// The API enforces the same split; this stops the UI offering pages that would only answer 403.
function RoleGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const me = useQuery({ queryKey: ["auth", "me"], queryFn: authApi.me, retry: false });

  const roles = me.data?.roles;
  const needed = roleForPath(pathname);
  const allowed = !roles || needed === null || roles.includes(needed);
  const home = roles ? homeFor(roles) : null;

  useEffect(() => {
    if (!allowed && home) router.replace(home);
  }, [allowed, home, router]);

  if (me.isLoading) return <LoadingState />;
  if (allowed) return <>{children}</>;
  if (home) return <LoadingState label="Taking you to your page…" />;
  return (
    <EmptyState
      title="Your account has no access here"
      description="Your account does not hold a role for any part of this application. Ask an administrator to grant you access."
    />
  );
}

// Layout chrome. A client component so auth state (banner, sign-in gate,
// sign-out) can live alongside the sidebar; the root layout stays a server
// component.
export function AppShell({ children }: { children: React.ReactNode }) {
  const oidc = AUTH_MODE === "oidc";
  const shell = (
    <>
      <AuthBanner onSignIn={oidc ? () => signIn() : undefined} />
      <Sidebar onSignOut={oidc ? () => signOut() : undefined} />
      <main className="ml-64 min-h-screen">
        <div className="mx-auto max-w-6xl px-8 py-8">
          <RoleGate>{children}</RoleGate>
        </div>
      </main>
    </>
  );
  return oidc ? <OidcSession>{shell}</OidcSession> : shell;
}
