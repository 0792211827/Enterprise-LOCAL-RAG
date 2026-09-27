"use client";
import { AuthBanner } from "@/components/AuthStatus";
import { Sidebar } from "@/components/Sidebar";

// Layout chrome. A client component so auth state (banner, sign-out) can live
// alongside the sidebar; the root layout itself stays a server component.
export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <AuthBanner />
      <Sidebar />
      <main className="ml-64 min-h-screen">
        <div className="mx-auto max-w-6xl px-8 py-8">{children}</div>
      </main>
    </>
  );
}
