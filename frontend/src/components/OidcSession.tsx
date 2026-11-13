"use client";
import { useQueryClient } from "@tanstack/react-query";
import type { User } from "oidc-client-ts";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { setTokenProvider } from "@/lib/auth";
import { OIDC_CALLBACK_PATH, completeSignIn, oidcConfigError, signIn, userManager } from "@/lib/oidc";

type Status = "loading" | "signed-in" | "signed-out" | "error";

// Holds the admin UI until the user has signed in with the IdP, and keeps the
// API client's bearer token in step with the (in-memory) OIDC session.
export function OidcSession({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const configError = oidcConfigError();
    if (configError) {
      setError(configError);
      setStatus("error");
      return;
    }
    const um = userManager();

    const apply = (user: User | null) => {
      if (user && !user.expired) {
        setTokenProvider(async () => (await um.getUser())?.access_token ?? null);
        setStatus("signed-in");
        queryClient.invalidateQueries();
      } else {
        setTokenProvider(null);
        setStatus("signed-out");
      }
    };
    const onLoaded = (user: User) => apply(user);
    const onGone = () => apply(null);
    um.events.addUserLoaded(onLoaded);
    um.events.addUserUnloaded(onGone);
    um.events.addAccessTokenExpired(onGone);

    if (pathname === OIDC_CALLBACK_PATH) {
      completeSignIn()
        .then(({ user, returnTo }) => {
          apply(user);
          // Client-side navigation: a full page load would drop the in-memory session.
          router.replace(returnTo);
        })
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : String(err));
          setStatus("error");
        });
    } else {
      um.getUser().then(apply);
    }

    return () => {
      um.events.removeUserLoaded(onLoaded);
      um.events.removeUserUnloaded(onGone);
      um.events.removeAccessTokenExpired(onGone);
    };
    // Runs once per page load; later navigation keeps the session in memory.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (status === "signed-in") return <>{children}</>;

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50">
      <div className="card w-96 p-8 text-center">
        <div className="mx-auto mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-brand-600 font-bold text-white">
          R
        </div>
        <h1 className="text-base font-semibold">Enterprise Local RAG</h1>
        <p className="mt-1 text-sm text-slate-500">Admin Control Plane</p>
        {status === "loading" && <p className="mt-6 text-sm text-slate-400">Checking your session…</p>}
        {status === "signed-out" && (
          <button className="btn btn-primary mt-6 w-full" onClick={() => signIn()}>
            Sign in with SSO
          </button>
        )}
        {status === "error" && (
          <>
            <p className="mt-6 text-sm text-red-600">Sign-in failed: {error}</p>
            <button className="btn btn-secondary mt-4 w-full" onClick={() => signIn("/")}>
              Try again
            </button>
          </>
        )}
      </div>
    </div>
  );
}
