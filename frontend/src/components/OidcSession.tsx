"use client";
import { useQueryClient } from "@tanstack/react-query";
import type { User } from "oidc-client-ts";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { CheckingScreen, SignInFailedScreen, SignedOutScreen } from "@/components/AccessScreen";
import { setTokenProvider } from "@/lib/auth";
import { OIDC_CALLBACK_PATH, completeSignIn, oidcConfigError, signIn, userManager } from "@/lib/oidc";

type Status = "loading" | "signed-in" | "signed-out" | "error";

// Holds the app until the user has signed in with the IdP, and keeps the
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
  if (status === "loading") return <CheckingScreen />;
  if (status === "signed-out") return <SignedOutScreen onSignIn={() => signIn()} />;
  return <SignInFailedScreen detail={error ?? "Unknown error"} onRetry={oidcConfigError() ? undefined : () => signIn("/")} />;
}
