import type { Page } from "@playwright/test";

// A stand-in identity provider for the OIDC build: discovery, authorize, token and end-session,
// answered at the HTTP boundary like the API. The app is a public client using the authorization
// code flow with PKCE, so authorize simply bounces straight back with a code, and the token endpoint
// returns an id token carrying the nonce the app asked for. The client does not verify the token's
// signature (it trusts the TLS-protected token endpoint), so the token is unsigned.

export const IDP_ORIGIN = "http://idp.stub.test";

const cors = { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "GET,POST,OPTIONS" };

const b64 = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");

export interface IdpStub {
  /** The authorize requests the app made, so a test can see the sign-in really went to the IdP. */
  authorizeRequests: URL[];
  /** Make the next authorize call come back as an IdP error, e.g. "access_denied". */
  failNextSignIn(error: string): void;
}

export async function stubIdp(page: Page, subject = "maria.santos@example.test"): Promise<IdpStub> {
  const stub: IdpStub = { authorizeRequests: [], failNextSignIn: (error) => (failure = error) };
  let failure: string | null = null;
  let nonce = "";

  await page.route(`${IDP_ORIGIN}/**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });

    switch (url.pathname) {
      case "/.well-known/openid-configuration":
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          headers: cors,
          body: JSON.stringify({
            issuer: IDP_ORIGIN,
            authorization_endpoint: `${IDP_ORIGIN}/authorize`,
            token_endpoint: `${IDP_ORIGIN}/token`,
            end_session_endpoint: `${IDP_ORIGIN}/logout`,
            jwks_uri: `${IDP_ORIGIN}/jwks`,
          }),
        });
      case "/authorize": {
        stub.authorizeRequests.push(url);
        nonce = url.searchParams.get("nonce") ?? "";
        const back = new URL(url.searchParams.get("redirect_uri")!);
        back.searchParams.set("state", url.searchParams.get("state") ?? "");
        if (failure) {
          back.searchParams.set("error", failure);
          failure = null;
        } else {
          back.searchParams.set("code", "stub-code");
        }
        return route.fulfill({ status: 302, headers: { location: back.toString() } });
      }
      case "/token": {
        const now = Math.floor(Date.now() / 1000);
        const idToken = [
          b64({ alg: "none", typ: "JWT" }),
          b64({ iss: IDP_ORIGIN, sub: subject, aud: "rag-ui", iat: now, exp: now + 3600, nonce }),
          "",
        ].join(".");
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          headers: cors,
          body: JSON.stringify({
            access_token: "stub-access-token",
            token_type: "Bearer",
            expires_in: 3600,
            scope: "openid profile email",
            id_token: idToken,
          }),
        });
      }
      case "/logout": {
        const back = url.searchParams.get("post_logout_redirect_uri") ?? "/";
        return route.fulfill({ status: 302, headers: { location: back } });
      }
      default:
        return route.fulfill({ status: 404, headers: cors, body: "" });
    }
  });
  return stub;
}
