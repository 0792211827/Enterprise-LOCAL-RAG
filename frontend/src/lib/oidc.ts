// OIDC sign-in for the admin UI (NEXT_PUBLIC_AUTH_MODE=oidc): authorization
// code flow with PKCE against the buyer's IdP, as a public client.
//
// Tokens are held in memory only, never in localStorage, where any script
// injected into the page could read them. Reloading the page therefore signs
// in again; with an active IdP session that is a redirect round-trip, not a
// password prompt. Only the short-lived PKCE/state values touch sessionStorage.

import { InMemoryWebStorage, User, UserManager, WebStorageStateStore } from "oidc-client-ts";

const ISSUER = process.env.NEXT_PUBLIC_OIDC_ISSUER || "";
const CLIENT_ID = process.env.NEXT_PUBLIC_OIDC_CLIENT_ID || "";
const SCOPE = process.env.NEXT_PUBLIC_OIDC_SCOPE || "openid profile email";

export const OIDC_CALLBACK_PATH = "/auth/callback";

let manager: UserManager | null = null;

export function oidcConfigError(): string | null {
  if (!ISSUER || !CLIENT_ID) {
    return "NEXT_PUBLIC_OIDC_ISSUER and NEXT_PUBLIC_OIDC_CLIENT_ID must be set at build time.";
  }
  return null;
}

/** The singleton UserManager. Browser-only: call from effects or handlers. */
export function userManager(): UserManager {
  if (!manager) {
    const origin = window.location.origin;
    manager = new UserManager({
      authority: ISSUER,
      client_id: CLIENT_ID,
      scope: SCOPE,
      redirect_uri: `${origin}${OIDC_CALLBACK_PATH}`,
      post_logout_redirect_uri: `${origin}/`,
      response_type: "code",
      userStore: new WebStorageStateStore({ store: new InMemoryWebStorage() }),
      stateStore: new WebStorageStateStore({ store: window.sessionStorage }),
      // Renews with the refresh token when the IdP issues one.
      automaticSilentRenew: true,
    });
  }
  return manager;
}

export function signIn(returnTo: string = window.location.pathname + window.location.search) {
  return userManager().signinRedirect({ state: { returnTo } });
}

export function signOut() {
  return userManager().signoutRedirect();
}

/** Complete the redirect back from the IdP; returns where to go next. */
export async function completeSignIn(): Promise<{ user: User; returnTo: string }> {
  const user = await userManager().signinRedirectCallback();
  const state = user.state as { returnTo?: string } | undefined;
  // Only same-origin paths: never bounce to an attacker-supplied absolute URL.
  const returnTo = state?.returnTo?.startsWith("/") && !state.returnTo.startsWith("//") ? state.returnTo : "/";
  return { user, returnTo };
}
