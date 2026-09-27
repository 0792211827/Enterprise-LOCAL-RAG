"use client";
import { AuthBanner } from "@/components/AuthStatus";
import { OidcSession } from "@/components/OidcSession";
import { Sidebar } from "@/components/Sidebar";
import { AUTH_MODE } from "@/lib/auth";
import { signIn, signOut } from "@/lib/oidc";

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
        <div className="mx-auto max-w-6xl px-8 py-8">{children}</div>
      </main>
    </>
  );
  return oidc ? <OidcSession>{shell}</OidcSession> : shell;
}
