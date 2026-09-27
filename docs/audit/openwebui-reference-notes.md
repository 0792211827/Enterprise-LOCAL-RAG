# Open WebUI reference notes: auth, SSO, knowledge access, audit (Phase 2)

*Security track. The reference is Open WebUI (OWUI) at commit `8bd8b4f`
(2026-09-21), cloned read-only to `../reference/open-webui`. Paths prefixed
`owui:` are relative to `owui:backend/open_webui/`. Our paths are relative to
`backend/`. **No code was copied.** Each section describes OWUI's pattern in our
own words, what adopting an equivalent would need in our stack (see
`current-architecture-gaps.md` for insertion points I1–I14), and how invasive
that would be. The key claims were spot-checked against source.*

## TL;DR

| Area | OWUI's pattern | What we should take | What we should do differently |
|------|----------------|---------------------|-------------------------------|
| Auth & RBAC | OWUI is the identity provider: a local user table, a fixed 3-role string (`admin`/`user`/`pending`), group-scoped boolean feature permissions OR-merged, and its own HS256 JWT. | The shape: a principal resolved per request, a coarse role gate via FastAPI dependencies, and a fresh DB lookup so role changes apply immediately. | **Don't become the IdP.** We sit behind the buyer's SSO. We validate *their* token (JWKS) per request instead of minting our own sessions. |
| SSO | Authlib OIDC login → claims mapped to a local user/role/groups → **OWUI issues its own JWT**. The IdP token is never checked again. There's also trusted-header, LDAP and SCIM. | Claim-mapping config (`ROLES_CLAIM`, `ADMIN_ROLES`, `GROUPS_CLAIM`, dotted claim paths) and the trusted-header mode as a simple option. | Stateless bearer validation per request. No login UI, no refresh-token storage, no local passwords. |
| Knowledge access | Owner + an `access_grant` table (resource, principal=user/group/anyone, read/write). **Access is re-checked at every retrieval, as the caller.** A model's grant does *not* extend to its KBs, which are silently dropped. Isolation is per collection, with no chunk-level ACL. | The grant table design, re-checking at query time, silent-drop semantics, and the "unknown scope = deny" default. | An admin bypass that can be switched off *everywhere* (OWUI's is inconsistent), plus an optional document-level tag filter in OpenSearch that OWUI lacks. |
| Audit | HTTP middleware → a loguru JSON-lines file. Off by default, and **chat/RAG paths are excluded by default**. No DB table, no query API, no UI, no retrieval facts. | Levels (metadata / request / request+response), a body-size cap, password redaction, and an event catalogue with actor snapshots. | A **DB-backed, synchronous, retrieval-aware** audit event (actor, scope decision, chunk IDs, answer hash) with an admin query/export API. This is where we can clearly beat OWUI. |

**Positioning takeaway.** OWUI's security model is built for *being the app
users log into*. Ours should be built for *being embedded behind the buyer's
existing identity and perimeter*. The patterns worth borrowing are the
authorization data model and query-time re-checking. The session and identity
machinery is what we deliberately *don't* build.

---

## 1. Auth & RBAC

### Pattern observed

- **Users and roles.** There is a `user` table with a `role` string column that
  defaults to `pending` (`owui:models/users.py:142-176`) and a separate `auth`
  table holding the password hash and an `active` flag
  (`owui:models/auths.py:26-34`).
  - Roles are three string literals with no enum: `admin`, `user`,
    `pending`. The set of verified roles is defined at
    `owui:utils/auth.py:567`.
  - The first user to sign up is promoted to admin and further signup is
    disabled (`owui:routers/auths.py:862-880`).
  - There are no custom roles. Everything finer-grained comes from groups
    and resource grants.
- **Groups carry feature permissions.** A `group` table has a `permissions`
  JSON of nested booleans, and a `group_member` table links users to groups
  (`owui:models/groups.py:37-86`). The keys look like
  `workspace.knowledge`, `sharing.public_knowledge`, `chat.file_upload` and
  `features.api_keys`. The defaults live in `DEFAULT_USER_PERMISSIONS`
  (`owui:config.py:1940-2020`) and each one can be overridden by an env var.
- **How permissions are evaluated.**
  - The effective permissions are the global defaults OR every group's
    permissions (`owui:utils/access_control/__init__.py:36-72`). The merge
    is "most permissive wins", so **a group can only add permissions, never
    remove one.**
  - To restrict a feature you set the default to False and grant it back
    through a group.
  - Admins skip permission checks *in each route* (`user.role != 'admin' and
    not has_permission(...)`), not centrally.
- **Sessions.**
  - OWUI signs its own HS256 JWT with `WEBUI_SECRET_KEY`. The payload is
    just `{id, exp, jti, iat}` (`owui:utils/auth.py:232-243`); role and email
    are not in the token.
  - `get_current_user` accepts the token from a Bearer header, a cookie or
    `x-api-key`. It decodes it, checks a Redis revocation list, and **loads
    the user from the DB on every request**, so a role change takes effect on
    the next call (`owui:utils/auth.py:393-506`).
  - Revocation needs Redis and fails open if Redis errors
    (`owui:utils/auth.py:283,309`).
- **Enforcement.** Routes use FastAPI dependencies: `get_verified_user` (role
  user or admin) and `get_admin_user` (role admin), for example
  `owui:routers/groups.py:58-64`. Feature checks are inline
  `has_permission(...)` calls, as in `owui:routers/knowledge.py:318-343`.
- **API keys.**
  - Keys are per-user, formatted `sk-` + a uuid4 hex string, and **stored in
    plaintext** (`owui:models/users.py:244`). The `expires_at` field is not
    enforced at lookup.
  - API keys are off by default (`ENABLE_API_KEYS`), and there is an optional
    allowlist of endpoints a key may call.
- **No-auth mode.** `WEBUI_AUTH=False` auto-logs everyone in as a fixed
  `admin@localhost` account. It only works while there is a single user.

### What adopting an equivalent would need in our stack

- **A request principal.** Add a `Principal` value object (subject, roles,
  groups, tenant, and auth method: `anonymous`, `bearer-jwt`, `api-key` or
  `trusted-header`). Resolve it with a `get_principal` dependency and expose it
  as `PrincipalDep` in `dependencies.py`. This fills gaps I1/I2.
- **Role gates as dependencies, applied per router.** Use
  `APIRouter(dependencies=[Depends(require_role(Role.ADMIN))])` on the admin
  CRUD routers (knowledge bases, documents, applications, providers,
  retrieval, API keys, system: I13) and `require_role(Role.QUERY)` on the
  answer paths. Applying them at router level rather than in every handler
  avoids OWUI's scattered `role != 'admin'` checks.
- **Few roles.** Three are enough to start: `admin` (control plane),
  `querier` (the answer endpoints), and `auditor` (read-only access to the
  audit API). Derive them from token claims. Don't store roles in our DB unless
  the buyer has no IdP.
- **Off by default.** Behind `AUTH__MODE=disabled`, the default,
  `get_principal` returns an anonymous principal with every role. Every
  existing test and `make start` then behave exactly as today.
- **Keep what's already better than OWUI:** SHA-256-hashed keys, a one-time
  plaintext reveal, and revoke/rotate. Add enforcement behind a flag and bind
  each key to its application (I8/I9).

**Invasiveness: moderate, but additive.**
- New modules: `auth/` and a principal dependency.
- Routers gain a `dependencies=` argument.
- No existing signatures or tables change.
- The existing API tests keep passing because the default mode is
  permissive.

---

## 2. SSO / identity intake

### Pattern observed

- **OIDC login.** OWUI uses the Authlib Starlette client with preset providers
  (Google, Microsoft/Entra, GitHub, Feishu) plus **one** generic OIDC provider
  configured through a discovery URL, client ID, and client secret or PKCE
  (`owui:config.py:2653-2781`). On callback it:
  1. verifies the ID token against the provider's JWKS, evicting the JWKS
     cache and retrying once after a signature failure
     (`owui:utils/oauth.py:1889-1922`);
  2. maps claims to a local user;
  3. **mints its own OWUI JWT.** The cookie is set with `httponly=False`
     (`owui:utils/oauth.py:2192-2194`).

  After that, **the IdP token is never checked again.** The OWUI session is
  independent: its lifetime defaults to 4 weeks, and a role or group change at
  the IdP only takes effect at the next login. The only exception is optional
  OIDC back-channel logout, which needs Redis. IdP tokens are kept Fernet-
  encrypted in `oauth_session`, but only to make **outbound** calls on the
  user's behalf.
- **Claim mapping** (`owui:utils/oauth.py:1531-1800`):
  - Configurable claims: subject, email, username, a roles claim (dotted
    paths allowed), and a groups claim.
  - Role mapping: `OAUTH_ALLOWED_ROLES` maps to `user` and
    `OAUTH_ADMIN_ROLES` maps to `admin`. **A roles claim that is present but
    matches nothing gets a 403.** A missing claim keeps the user's existing
    role.
  - Groups are matched by name and can be auto-created. The user is removed
    from groups not in the claim, but only when the claim is non-empty.
  - Merging an OIDC login into an existing account by email is opt-in.
  - A domain allowlist restricts which email domains may sign in.
- **Other intake methods:**
  - **Trusted header.** An env var names a header, such as
    `WEBUI_AUTH_TRUSTED_EMAIL_HEADER`, plus optional name, groups and role
    headers. OWUI trusts the header on sign-in and auto-creates the user. On
    later requests it rejects a JWT whose email differs from the header
    (`owui:utils/auth.py:462-468`).
  - **LDAP bind** (`owui:routers/auths.py:475-714`).
  - **SCIM 2.0 provisioning** for users and groups, authenticated with one
    static bearer token (`owui:routers/scim.py`).
  - **Token exchange** (off by default). An external access token is
    validated **once** via the provider's userinfo endpoint and exchanged for
    an OWUI JWT.

### What adopting an equivalent would need in our stack

Our model is "the host application or gateway already authenticated the user;
we accept its assertion". That is a **resource-server** pattern, not OWUI's
**client/session-owner** pattern.

- **Mode `jwt`.** Accept `Authorization: Bearer <JWT>` issued by the buyer's
  IdP. Validate the signature against a configured `AUTH__JWKS_URL` and check
  `AUTH__ISSUER`, `AUTH__AUDIENCE`, expiry and not-before.
  - Use PyJWT's `PyJWKClient`, which caches keys, with OWUI's "refetch once on
    unknown `kid`" behaviour.
  - This is **stateless and runs on every request**, so a role revoked at the
    IdP takes effect when the token expires.
  - The JWKS URL points at the buyer's in-perimeter IdP. That is a call to
    customer infrastructure, not an external AI API, so it stays within our
    positioning.
  - Support a static PEM public key (`AUTH__PUBLIC_KEY_PATH`) for fully
    air-gapped deployments.
- **Claim mapping.** Borrow OWUI's configuration vocabulary:
  `AUTH__SUBJECT_CLAIM`, `AUTH__ROLES_CLAIM` (dotted path),
  `AUTH__GROUPS_CLAIM`, `AUTH__ADMIN_ROLES`, `AUTH__QUERY_ROLES`,
  `AUTH__AUDITOR_ROLES` and `AUTH__TENANT_CLAIM`. Like OWUI, a token with no
  mappable role is denied.
- **Mode `trusted_header`.** For deployments behind an authenticating reverse
  proxy such as oauth2-proxy, an Istio/Envoy ext-authz filter or an F5 in
  front of a legacy app, read the identity and groups from configured headers.
  Document plainly that this mode is only safe when the API port is reachable
  solely through that proxy.
- **Machine clients.** Mode `jwt` combines with enforced per-application API
  keys for machine-to-machine calls on `/v1/chat/completions`.
- **Not building:** a login UI, refresh-token storage, local passwords, LDAP
  or SCIM. Directory sync is the IdP's job. SCIM could be revisited if buyers
  need offline group sync for ACLs.
- **No users table required.** If needed later, an optional
  `principals_seen` table can record the subjects we have observed, for
  audit display names.

**Invasiveness: additive.**
- A new `AuthSettings` group (`AUTH__` prefix) and one new dependency
  (`PyJWT[crypto]`).
- No schema changes.
- The default `disabled` mode keeps today's behaviour.

---

## 3. Access control on knowledge and retrieval

**This is our biggest gap, and it's where OWUI's design is most worth
studying.**

### Pattern observed

- **Ownership and grants.**
  - Each knowledge base has an owner (`Knowledge.user_id`).
  - Sharing lives in a generic `access_grant` table with the columns
    resource type, resource ID, principal type (`user`, `group` or `anyone`),
    principal ID (`*` means everyone), and permission (`read` or `write`),
    with a uniqueness constraint (`owui:models/access_grants.py:25-45`).
  - An earlier JSON `access_control` column was migrated into grant rows by
    an Alembic migration and then dropped
    (`owui:migrations/versions/f1e2d3c4b5a6_add_access_grant_table.py`).
  - A single helper, `has_permission_filter`
    (`owui:models/access_grants.py:723-830`), turns "owner OR a matching
    grant" into a SQL `EXISTS` subquery for list endpoints.
- **Re-checked at query time, as the caller.** Every chat turn that touches a
  knowledge base re-validates the *caller's* read access before any vector
  query:
  - the legacy RAG path (`owui:retrieval/utils.py:1566-1580`);
  - a final `filter_accessible_collections` gate
    (`owui:retrieval/utils.py:1672-1677`, defined at `:1299-1362`);
  - the native tool-calling path (`owui:tools/builtin.py:3221-3304`).

  Unknown collection names are denied by default, and collection names are
  validated against a safe character set.
- **Model access doesn't grant knowledge access.** If a user may use model X
  and X has knowledge base K attached, but the user has no grant on K, then
  **K is silently dropped from retrieval**; no error is raised. The exception
  is files attached directly to a model, where access to the model *does*
  authorize the file (`owui:tools/builtin.py:113-116`).
- **Isolation is per collection, never per chunk.** Each knowledge base (and
  each file) is its own vector collection. `query_doc` searches with no
  metadata filter (`owui:retrieval/utils.py:344-355`). Chunks carry
  `created_by` and `file_id` metadata, but those fields are only used for
  deduplication and deletion. **There is no document-level or chunk-level
  permission filtering.**
- **The admin bypass is inconsistent.** `BYPASS_ADMIN_ACCESS_CONTROL`, default
  true, only affects *list* views. Read, write and delete endpoints, and every
  retrieval check, use a hardcoded `role == 'admin'`. Turning the bypass off
  hides content from admins in lists, but they can still open and query any
  knowledge base by ID.

### What adopting an equivalent would need in our stack

- **A grant table.** Add a `knowledge_base_grants` table with columns `id`,
  `knowledge_base_id` (FK, cascade), `principal_type` (`group`, `user`, `role`
  or `everyone`), `principal_value` (the claim value, e.g. an IdP group name),
  `permission` (`read`; `manage` later), `created_at`, `created_by`, and a
  uniqueness constraint.
  - We key on **IdP claim values**, not local user IDs, because we don't own
    users.
  - Because it's a new table, `create_all` creates it on existing
    deployments. No Alembic is needed yet.
- **One resolver, used on every path.**
  `resolve_permitted_kb_ids(principal, session) -> set[str] | ALL`.
  - When auth is disabled, return `ALL`.
  - When auth is on, return the knowledge bases granted to any of the
    principal's groups, subject or roles.
  - Intersect the result with the request's scope at every retrieval call
    site:
    - the application path: application KBs ∩ permitted (I10);
    - legacy `/ask` and `/stream`: permitted only (I5);
    - the agentic tool: pass the scope via `Context` (I11);
    - hybrid search (I12).
  - **Fail closed.** An empty intersection returns nothing, reusing the
    existing `answer.py:70` pattern.
  - Enforce this in `OpenSearchClient.search_unified` and
    `search_chunks_vector`: a new `strict_scope=True` mode that raises if no
    `knowledge_base_ids` are given. That gives a single chokepoint so a future
    caller can't forget the filter. That's the lesson from OWUI's
    `filter_accessible_collections` gate.
- **Silent drop for application KBs.** Copy OWUI's semantics: KBs attached to
  an application but not permitted for the caller are dropped silently, and
  the audit record notes which were dropped.
- **Cache isolation.** Include the permitted scope's hash in the Redis cache
  key (I4). Without this, permission filtering is bypassed through the cache.
- **Beyond OWUI (phase 2 of this feature): document-level tags.**
  - Add an `acl_tags` keyword field to the chunk mapping. `PUT _mapping` is
    additive even on a strict mapping.
  - Populate it at ingest from a new side table, `document_acl_tags`, since we
    can't add columns without migrations.
  - Filter with `terms: {acl_tags: principal.groups}` OR `must_not exists
    acl_tags`, so untagged chunks fall back to KB-level permission only.
  - This works on the shared index because our KB filter is already a term
    filter. OWUI's per-collection design can't do this.
  - Watch the **nmslib post-filter** issue: the oversampling in
    `opensearch/client.py:16-33` has to cover the combined selectivity, or
    better, switch to the `lucene` or `faiss` engine with a reindex. That is
    a separate, explicit decision.
- **Admin bypass.** Make it *one* flag checked in the resolver
  (`AUTH__ADMIN_BYPASS_RETRIEVAL`, default false when auth is on), so the
  answer is consistent across list, read and retrieval.

**Invasiveness.**
- **KB-level grants: moderate.** It needs a new table, a resolver, and
  touches at about five call sites. It is non-breaking by default because
  the disabled mode resolves to `ALL`.
- **Document-level tags: moderate to large.** It needs a mapping update,
  ingest changes, and a backfill or "untagged = KB-level" rule. It's
  additive but should come after KB-level grants.

---

## 4. Audit logging

### Pattern observed

- **HTTP middleware** (`owui:utils/audit.py:116-307`), mounted only when
  `AUDIT_LOG_LEVEL` isn't `NONE`, which is the default.
  - Levels are `METADATA`, `REQUEST` and `REQUEST_RESPONSE`.
  - Each entry holds an ID, a user snapshot (id, name, email, role), verb,
    URI, user agent, source IP (`request.client.host` only; forwarded
    headers aren't parsed), and, depending on level, the request body, the
    response body and the status code (`owui:utils/audit.py:37-50`).
  - Bodies are capped at 2 KB and passwords are redacted by regex.
  - By default only write verbs are logged; GET is opt-in.
- **Chat and RAG are excluded by default.** `AUDIT_EXCLUDED_PATHS` defaults to
  `/chats,/chat,/folders` (`owui:env.py:1243-1249`). The path match is a
  regex with a word boundary, which also excludes `/api/chat/completions`. **So
  the one flow a compliance team cares most about isn't audited out of the
  box.**
- **Written to a file only.** Entries go to a loguru JSON-lines file with size
  rotation and zip compression, and optionally to stdout/OTEL. There's **no DB
  table, no query or export API, no admin UI and no retention setting.**
- **No retrieval facts.** No chunk IDs and no permission decisions are
  recorded. Denials are debug log lines.
- **Adjacent records that do exist:**
  - `chat_message.sources` persists the retrieved chunk *text and metadata*
    per assistant message. It's the de facto "what was retrieved" record, but
    it's mutable (users can delete chats) and temporary chats aren't saved.
  - An admin-only analytics API over messages.
  - A feedback table with a full chat snapshot.
  - An event bus (`owui:events.py`) with actor snapshots for `auth.login`,
    `user.role_updated`, `knowledge.access_updated` and so on. It only fans
    out to sockets, webhooks and plugins; nothing is persisted.

### What adopting an equivalent would need in our stack

This is where we can **clearly exceed** OWUI. Our answer path is narrow and
already centralized, so a retrieval-aware audit record is cheap to produce.

- **An `audit_events` table** (new, so `create_all` creates it). One row per
  answered request or admin mutation:
  - **When and where:** `id`, `occurred_at` (UTC, server-set),
    `request_id`, `source_ip`, `user_agent`.
  - **Who:** `actor_subject`, `actor_roles`, `actor_groups`,
    `auth_method`, `api_key_id`, `application_id`.
  - **What:** `action` (e.g. `query.answer`, `query.search`,
    `kb.create`, `kb.grant.update`, `document.delete`,
    `api_key.rotate`) and `resource_type`/`resource_id`.
  - **The permission decision:** `requested_kb_ids`, `permitted_kb_ids`
    and `dropped_kb_ids`.
  - **What was retrieved:** `retrieved` as JSON, `[{chunk_id,
    document_id, kb_id, score}]`. IDs only, no text, to limit data
    duplication.
  - **The answer:** `query_text`, `answer_text` or an `answer_sha256`
    depending on the capture level, and `cache_hit`.
  - **Model and outcome:** `model`, `outcome` (`ok`, `denied`, `error`),
    `error_code`, `latency_ms`.
- **Written synchronously on the request path.** The write happens in the same
  request, after the answer is produced and before the response is returned
  (for streaming: when the stream completes, from values captured up front).
  Unlike Langfuse, a failed audit write is **not silently swallowed**.
  `AUDIT__FAIL_MODE=log|deny` lets strict deployments refuse to answer when
  auditing fails.
- **One recorder, every answer path.** A single helper,
  `record_query_audit(...)`, is called from `answer.py` (both the playground
  and `/v1`), legacy ask/stream, agentic and hybrid search. Admin mutations
  are audited by a small router-level dependency or explicit calls in the CRUD
  routers.
- **Capture levels** borrowed from OWUI: `AUDIT__LEVEL=off|metadata|full`.
  At `metadata` we store IDs and hashes but not the query or answer text; at
  `full` we store the text too. `off` is the default, so behaviour is
  unchanged.
- **An admin API for auditors.** `GET /api/v1/audit/events` with filters
  (actor, time range, action, KB, application), plus
  `GET /api/v1/audit/events/export` streaming NDJSON or CSV. Both require the
  `auditor` or `admin` role.
- **Correlation.** A request-ID middleware adds `X-Request-ID` and stamps it
  on the audit row, the Langfuse trace metadata and log lines, so the three
  records can be joined. Langfuse stays optional observability.
- **Later: tamper evidence.** A hash chain in which each row stores the
  previous row's hash, and/or an append-only DB role with no UPDATE or DELETE
  grant. Retention through a configurable purge job.

**Invasiveness: additive.**
- New table, new recorder, one new router, and a request-ID middleware.
- The answer-path functions gain an optional `audit_ctx` parameter with a
  default of None, so existing callers and tests are unaffected.
- The main risk is latency: one INSERT per request, which is negligible next
  to LLM generation.

---

## Cross-cutting lessons from OWUI (things to avoid)

1. **Scattered admin checks.** OWUI repeats `role == 'admin'` across dozens of
   routes and retrieval helpers, which is why its admin-bypass flag is
   inconsistent. Centralize this: one `require_role` dependency and one scope
   resolver.
2. **OR-only permission merge.** Groups can't revoke. For KB grants, make the
   rule explicit: grants only add access, and nothing is readable without a
   grant once auth is on. That's intended, and it's documented rather than
   emergent.
3. **Plaintext API keys and unenforced expiry.** We already hash keys. We
   should add enforcement, binding each key to its application, and optional
   expiry.
4. **Revocation that fails open.** OWUI's JWT revocation fails open when Redis
   is down. With stateless IdP tokens we avoid needing a revocation store:
   short token lifetimes are the buyer's IdP's policy.
5. **Auditing off, or excluding chat, by default.** When auditing is enabled
   for us, the answer path must be covered first, not excluded.
6. **Perimeter.** None of this helps if OpenSearch, Postgres and Redis are
   reachable directly (see Phase 1 §2). OWUI has the same exposure with an
   external vector DB. A hardened compose profile binding infrastructure to
   the internal network only belongs in the security-track plan.
