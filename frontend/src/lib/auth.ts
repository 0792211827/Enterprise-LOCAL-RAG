// How the admin UI authenticates to the API. Baked in at build time, like
// NEXT_PUBLIC_API_URL:
//   none   no credentials (backend AUTH__MODE=disabled)
//   proxy  UI and API sit behind the same authenticating reverse proxy; the
//          proxy's session cookie is sent with every request
//   oidc   the UI signs in against the IdP (authorization code + PKCE) and
//          sends the access token as a Bearer header; see oidc.ts

export type AuthMode = "none" | "proxy" | "oidc";

function parseMode(value: string | undefined): AuthMode {
  return value === "proxy" || value === "oidc" ? value : "none";
}

export const AUTH_MODE: AuthMode = parseMode(process.env.NEXT_PUBLIC_AUTH_MODE);

// Cookies are only needed when a proxy holds the session. Otherwise keep the
// browser default so nothing ambient rides along with API calls.
export const FETCH_CREDENTIALS: RequestCredentials = AUTH_MODE === "proxy" ? "include" : "same-origin";

type TokenProvider = () => Promise<string | null>;
let tokenProvider: TokenProvider | null = null;

/** Registered by the OIDC session once a user is signed in. */
export function setTokenProvider(provider: TokenProvider | null) {
  tokenProvider = provider;
}

export async function authHeaders(): Promise<Record<string, string>> {
  const token = tokenProvider ? await tokenProvider() : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// A 401 anywhere means the session is missing or expired. The API client
// reports it here and the AuthBanner offers the right way back in.
const authRequiredListeners = new Set<() => void>();

export function onAuthRequired(listener: () => void): () => void {
  authRequiredListeners.add(listener);
  return () => authRequiredListeners.delete(listener);
}

export function notifyAuthRequired() {
  authRequiredListeners.forEach((listener) => listener());
}
