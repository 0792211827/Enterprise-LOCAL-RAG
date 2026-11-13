# ADR-0001: Federate identity through the buyer's IdP; do not issue our own sessions

- **Status:** Accepted (implemented as S1). LDAP: **open, decision required**.
- **Context:** The platform is embedded behind a buyer's existing identity and perimeter. Open WebUI instead acts as the identity provider (local users, passwords, own JWT).
- **Decision:** `AUTH__MODE` is `disabled` (default), `jwt` (validate buyer-issued tokens via JWKS or a static PEM for air-gapped installs) or `trusted_header` (identity asserted by an authenticating reverse proxy, honoured only from `AUTH__TRUSTED_PROXY_CIDRS`). The platform never stores passwords or mints session tokens. Roles (`admin`, `querier`, `auditor`) and groups are mapped from configurable claim paths.
- **Consequences:** no user table, no password reset, no token revocation list (tokens are short-lived and validated on every request). Admin UI sign-in is an OIDC client only (`NEXT_PUBLIC_AUTH_MODE=oidc`).
- **Open question (LDAP):** not implemented. Recommendation: keep it out; buyers' IdPs already front LDAP. If a buyer requires direct LDAP, build it as one more authenticator that produces the same `Principal` and groups, not as an identity store. Needs product-owner approval.
- **Defaults:** open (`disabled`) to preserve existing behaviour. Operators must opt in; `GET /api/v1/system/security` reports the real state.
