# Integration plan (Phase 4)

> **Implementation status (2026-09-27).** S1, S2/S2a/F1, S3 and S4 are
> implemented on `feature/owui-gap-analysis`. S5, S3a and the whole
> Hardware/Backend track (H1–H5) have not started. Where S4 differs from the
> plan below, see "As built" at the end of the S4 section.

*This plan covers every **Blocker** and **Important** row from
`feature-gap-comparison.md`, using the decisions confirmed after Phase 3:*

- *Order: S1 → S2 (+S2a, minimal F1) → S3 → S4, with S5 alongside, and S3a later.*
- *Grants key on IdP **claim values** (groups, roles or subject). There is no local users table.*
- *Identity modes for v1 are **JWT/JWKS bearer** and **trusted header**.*
- *Minimal admin-UI token support (F1) ships as part of S2.*
- *The hardened compose overlay (S5) is in scope. `compose.yml` stays untouched.*
- *No Alembic yet. Every new persistent structure is a **new table**, which
  `Base.metadata.create_all` creates on existing deployments. No existing table
  gains a column.*

**Hardware/Backend track** sections (H1–H5) are at the end. They are staffed
and sequenced separately and don't depend on the security track.

## Global rules (apply to every feature)

1. **Off by default.** Every new setting defaults to today's open behaviour.
   A fresh clone runs `make start` / `make test` exactly as it does now.
2. **One green commit at a time.** Every commit leaves `make test` green and
   `make health` passing. After each *feature*, results go to you before the
   next one starts.
3. **Existing tests stay unmodified**, except where a section says otherwise
   and explains why. That explanation also goes in the commit message.
4. **Fixture additions** to `tests/api/conftest.py` (new `make_*` patches) are
   infrastructure, not test changes. CLAUDE.md prescribes them for new
   `app.state` services.
5. **No new outbound network calls by default.** The only new egress is the
   JWKS fetch in S1. It happens only when an operator sets `AUTH__JWKS_URL`,
   which should point at their in-perimeter IdP. A static public key
   (`AUTH__PUBLIC_KEY_PATH`) covers air-gapped deployments.
6. **New dependencies:** `pyjwt[crypto]` (S1) and `langchain-openai` (H2).
   Both are added with `uv add` in the commit that first needs them.
7. **Portable column types only** (`GUID`, `String`, `JSON`, `DateTime`), so
   the in-memory SQLite domain tests (`tests/api/routers/test_domain.py`
   pattern) can exercise new tables.
8. **Settings style** follows `config.py`: nested groups with a `__` prefix
   (`AUTH__`, `API_KEYS__`, `AUDIT__`), documented in `.env.example` and the
   README in the same commit as the feature.

## Estimated effort (one engineer, including tests)

| Security track | Days | | Hardware/Backend track | Days |
|---|---|---|---|---|
| S1 Identity intake | 4 | | H1 OpenAI-compatible backend verification | 2 (+ a GPU-host session) |
| S2 RBAC + S2a keys | 3 | | H3 GPU overlays + VRAM display | 2 |
| F1 minimal admin-UI auth | 3 | | H5 Embedding dimension from config | 1 |
| S3 Permission-aware retrieval | 5 | | H2 All paths through `LLMProvider` | 4 |
| S4 Audit log | 5 | | H2a Per-application provider | 3 |
| S5 Hardened overlay | 1 | | | |
| **Total** | **≈ 21** | | **Total** | **≈ 12** |
| *S3a Document ACL tags (later)* | *6–10* | | | |

---

# Security track 🔒

## S1. Identity intake

### 1. Design

- **`Principal`** (`src/auth/principal.py`) is a frozen dataclass with:
  - `subject: str`
  - `roles: frozenset[Role]`
  - `groups: frozenset[str]`
  - `raw_roles: frozenset[str]` (the unmapped claim values, used for grants)
  - `auth_method: Literal["disabled", "jwt", "trusted_header", "api_key"]`
  - `api_key_id: UUID | None`
  - `application_id: UUID | None`

  `Role` is an enum with `ADMIN`, `QUERIER` and `AUDITOR`. `ADMIN` implies
  `QUERIER`. Whether admins can also read the audit log is controlled by
  `AUTH__ADMIN_CAN_READ_AUDIT`, which defaults to `true`, so strict
  separation-of-duties deployments can turn it off.
- **`AuthSettings`** is a new `AUTH__` group in `config.py`:

  ```
  AUTH__MODE=disabled                 # disabled | jwt | trusted_header
  # jwt mode
  AUTH__JWKS_URL=                     # buyer's IdP JWKS (in-perimeter)
  AUTH__PUBLIC_KEY_PATH=              # alternative: static PEM, air-gapped
  AUTH__ISSUER=
  AUTH__AUDIENCE=
  AUTH__ALGORITHMS=RS256,ES256        # "none" and HS* are rejected at startup
  AUTH__LEEWAY_SECONDS=30
  AUTH__JWKS_CACHE_SECONDS=600
  # claim mapping (both modes)
  AUTH__SUBJECT_CLAIM=sub
  AUTH__ROLES_CLAIM=roles             # dotted paths allowed, e.g. realm_access.roles
  AUTH__GROUPS_CLAIM=groups
  AUTH__ADMIN_ROLES=rag-admin
  AUTH__QUERY_ROLES=rag-user
  AUTH__AUDITOR_ROLES=rag-auditor
  AUTH__ADMIN_CAN_READ_AUDIT=true
  # trusted_header mode
  AUTH__TRUSTED_HEADER_SUBJECT=X-Forwarded-User
  AUTH__TRUSTED_HEADER_GROUPS=X-Forwarded-Groups
  AUTH__TRUSTED_HEADER_ROLES=X-Forwarded-Roles
  AUTH__TRUSTED_HEADER_SEPARATOR=,
  AUTH__TRUSTED_PROXY_CIDRS=          # required in trusted_header mode
  ```

- **Resolution happens in a dependency, not a middleware.**
  `get_principal(request)` lives in `src/auth/dependencies.py` and is exposed
  as `PrincipalDep` in `dependencies.py`. Keeping it a dependency means tests
  can override it with `app.dependency_overrides`. It stores the principal on
  `request.state.principal` so S4's admin-audit middleware can read it.
  - **`disabled`:** returns `Principal.anonymous()`, with subject
    `"api_user"`, all roles and `auth_method="disabled"`. The subject is
    `"api_user"` deliberately, so Langfuse traces keep today's attribution
    value.
  - **`jwt`:** reads `Authorization: Bearer`. A value starting with `sk-` is
    an API key and is handled in S2a; anything else is validated with PyJWT:
    - check the signature against JWKS (`PyJWKClient`, cached, refetched once
      on an unknown `kid`, run in a thread) or against the PEM key;
    - check `iss`, `aud`, `exp` and `nbf` with the configured leeway;
    - map claims to a principal;
    - **reject with 401 if the token maps to no role.** OWUI does the same.
    - Returns 401 with `WWW-Authenticate: Bearer` on any failure. The response
      doesn't say which check failed; the server log does.
  - **`trusted_header`:** accepted only if `request.client.host` is inside
    `AUTH__TRUSTED_PROXY_CIDRS`; otherwise 401. The subject, groups and roles
    headers map to a principal, with the same "no role means 401" rule.
- **Request-context middleware** (`src/middleware/request_context.py`, a pure
  ASGI middleware). It:
  - reuses an incoming `X-Request-ID` if it's well-formed, otherwise
    generates a UUID4;
  - stores it on `request.state`;
  - echoes it in the response header;
  - captures `source_ip`. `X-Forwarded-For` is honoured only when the peer
    is in `AUTH__TRUSTED_PROXY_CIDRS`.

  The unused `src/middlewares.py` stub is left alone.
- **Startup validation in `lifespan`.** In `jwt` mode, startup fails fast with
  a clear error if neither the JWKS URL nor the public key is set, or if the
  issuer or audience is empty. In `trusted_header` mode it fails if no
  proxy CIDRs are set. In `disabled` mode with `ENVIRONMENT=production`, it
  logs a WARNING.
- **`GET /api/v1/auth/me`** returns the resolved principal (subject, roles,
  groups, auth method). The admin UI (F1) and operators use it to debug claim
  mapping.
- **Trace attribution.** `RAGTracer.trace_request("api_user", …)` in
  `routers/ask.py` and the `user_id="api_user"` default in the agentic path
  now receive `principal.subject`. The value is unchanged in `disabled` mode.

### 2. Data model changes

None. S1 is stateless.

### 3. Backward compatibility

`AUTH__MODE=disabled` is the default and yields the anonymous all-roles
principal, so every route behaves exactly as today. The only visible change in
default mode is the new `X-Request-ID` response header, which is additive.

### 4. Rollout order (commits)

1. Request-context middleware, plus tests.
2. `AuthSettings`, `Principal`, `Role`, `get_principal` (disabled mode only),
   `PrincipalDep` and `GET /api/v1/auth/me`.
3. `uv add pyjwt[crypto]`, then the JWT validator (PEM and JWKS), claim
   mapping and startup validation.
4. Trusted-header mode with the proxy-CIDR check.
5. Pass `principal.subject` into the Langfuse traces (legacy and agentic).
6. README section "Authentication" and the `AUTH__*` block in `.env.example`.

### 5. Test plan

- **New `tests/unit/auth/`:**
  - A locally generated RSA key pair, created in a fixture, signs test
    tokens. The JWKS is served by monkeypatching the `PyJWKClient` fetch, so
    there's **no network**.
  - Cases: valid token, expired, wrong `iss`, wrong `aud`, wrong key,
    `alg=none`, unknown `kid` (refetch then fail), dotted roles claim, no
    mappable role (401), and groups claim as a list or a delimited string.
  - Trusted header: peer inside and outside the CIDRs, missing subject
    header, and spoofed `X-Forwarded-For` from an untrusted peer.
  - Settings validation at startup for each mode.
  - The middleware echoes a valid `X-Request-ID`, replaces a malformed one,
    and generates one when absent.
- **API:** `/auth/me` in disabled mode returns the anonymous principal, and in
  jwt mode (settings overridden) returns the mapped claims.
- **Existing suite: unmodified.** The default mode is disabled.

### 6. Rollback

- Set `AUTH__MODE=disabled` and restart. That's instant and needs no data
  cleanup.
- Code rollback is `git revert` of the S1 commits. Nothing persistent was
  created.

---

## S2. RBAC on API routes, S2a API-key enforcement, and F1 minimal admin-UI auth

### 1. Design

- **`require_role(*roles)`** is a dependency factory in `src/auth/dependencies.py`.
  It depends on `get_principal` and raises 403 if the principal has none of
  the given roles. It passes automatically in `disabled` mode, because the
  anonymous principal has every role.
- **Route policy matrix.** It's applied **in `main.py` at `include_router`**
  (`app.include_router(x.router, dependencies=[AdminOnly])`), so individual
  router files don't change.

  | Policy | Routes |
  |--------|--------|
  | **Public** | `GET /api/v1/health` (orchestrator probes) |
  | **Querier** | `/api/v1/ask`, `/api/v1/stream`, `/api/v1/ask-agentic`, `/api/v1/feedback`, `/api/v1/hybrid-search`, `/api/v1/auth/me` |
  | **Querier or app key** | `POST /v1/chat/completions` (see S2a) |
  | **Admin** | `knowledge_bases`, `documents` (+ ingestion jobs), `applications` (including the playground `/ask`), `providers` (including the SSRF-capable `/test` and `/models`), `retrieval`, `api_keys`, `system` (`/system/health`, `/stats`, `/gpu`) |
  | **Auditor** | The audit API (added in S4) |

- **Policy coverage test.** A test walks `app.routes` and fails if any route
  isn't in the policy map. Any new route added without a deliberate policy
  choice breaks the build.
- **S2a: API-key enforcement.** Controlled by a new `ApiKeySettings` group:
  `API_KEYS__ENFORCE=false`.
  - **When `true`,** `/v1/chat/completions` requires `Authorization: Bearer
    sk-…`, and the key must:
    - exist;
    - not be revoked;
    - have `key.application_id == application.id`, which closes the current
      cross-application gap.

    Failures return an OpenAI-shaped 401 `invalid_api_key`, reusing
    `_openai_error`. The key produces a principal with
    `auth_method="api_key"`, role `QUERIER`, `api_key_id` and
    `application_id` set.
  - **When `AUTH__MODE=jwt` and the bearer token isn't an `sk-` key,** it's
    validated as an end-user JWT and needs the `QUERIER` role.
  - **Acting on behalf of an end user.** The host app authenticates with its
    application key and can pass the end user's token in a configurable header
    (`AUTH__USER_TOKEN_HEADER=X-User-Authorization`). That token is validated
    like a JWT and becomes the principal whose grants scope retrieval in S3.
    The key is still recorded in the audit (S4). This is the "we sit behind
    your app" flow; it works with stock OpenAI SDKs through `default_headers`.
  - **When enforcement is off,** today's `_record_key_usage` behaviour is
    kept exactly.
  - **UI and docs honesty.** The module docstrings in
    `routers/chat_completions.py` and `routers/api_keys.py`, and the caveat
    in `frontend/src/app/applications/[id]/EndpointPanel.tsx`, currently say
    "not enforced". They become conditional on the actual setting, which a new
    read-only `GET /api/v1/system/security` exposes (auth mode, key
    enforcement, audit level). Key expiry isn't added, because it would need a
    new column. It's deferred to the Alembic decision.
- **F1: minimal admin-UI auth.** It changes only `frontend/src/lib/api/client.ts`
  and adds a small auth module. There are three build-time modes, set with
  `NEXT_PUBLIC_AUTH_MODE`:
  - **`none`** (default): unchanged.
  - **`proxy`**: the UI is served behind the same authenticating reverse proxy
    as the API (the trusted-header deployment). `fetch` uses
    `credentials: "include"` so the proxy's session cookie flows. There's no
    token handling in the browser.
  - **`oidc`**: an authorization-code + PKCE login against the buyer's IdP,
    using `oidc-client-ts` with `NEXT_PUBLIC_OIDC_ISSUER`,
    `NEXT_PUBLIC_OIDC_CLIENT_ID` and `NEXT_PUBLIC_OIDC_SCOPE`. The access
    token is kept **in memory**, with silent renew, and never written to
    `localStorage`. `apiGet`, `apiSend` and `apiUpload` attach
    `Authorization`.

  In every mode, a 401 shows a "sign in" state and a 403 shows a "your role
  can't do this" state, both via `ApiError.status`. A role badge from
  `/auth/me` appears in the sidebar. The build args join the existing
  `NEXT_PUBLIC_API_URL` pattern in the frontend Dockerfile; `compose.yml`
  build args are untouched, because the overlay passes them.

### 2. Data model changes

None. `application_api_keys` is already sufficient for enforcement.

### 3. Backward compatibility

- In `AUTH__MODE=disabled` every `require_role` passes.
- `API_KEYS__ENFORCE=false` preserves today's permissive `/v1` behaviour.
- `NEXT_PUBLIC_AUTH_MODE=none` leaves the frontend unchanged.

### 4. Rollout order (commits)

1. `require_role`, the policy map applied in `main.py`, and the
   policy-coverage test.
2. `GET /api/v1/system/security` (read-only).
3. S2a: `API_KEYS__ENFORCE`, key-to-application binding, the key principal,
   and the on-behalf-of header.
4. Updated docstrings and the conditional `EndpointPanel` caveat.
5. F1 `proxy` mode (the smallest).
6. F1 `oidc` mode, plus 401/403 states and the role badge.
7. README and `.env.example`.

### 5. Test plan

- Parametrized API tests with an overridden principal in `jwt` mode:
  - querier → admin route: 403;
  - admin → admin route: 200;
  - auditor → query route: 403;
  - public health with no token: 200;
  - every route is covered by the policy map.
- S2a: with enforcement on,
  - no key: 401;
  - revoked key: 401;
  - key belonging to application A used on application B: 401;
  - valid key: 200 and `last_used_at` stamped;
  - on-behalf-of header present: the principal is the end user and the key
    ID is retained.

  With enforcement off, the existing behaviour is pinned: no key still
  returns 200.
- `npm run build` for each `NEXT_PUBLIC_AUTH_MODE` value. There's no frontend
  test suite; `build` is the type check.
- **Existing suite: unmodified.**

### 6. Rollback

- Set `AUTH__MODE=disabled` and/or `API_KEYS__ENFORCE=false` and restart.
- Rebuild the frontend with `NEXT_PUBLIC_AUTH_MODE=none`.
- `git revert` of the S2 commits. There's no data impact.

---

## S3. Permission-aware retrieval (knowledge-base level)

### 1. Design

- **Grant model.** A new table, `knowledge_base_grants`, links a KB to IdP
  claim values: *"members of group `finance-analysts` may read KB
  `q3-filings`."* The principal types are `group`, `role`, `subject` and
  `everyone`. v1 has only the `read` permission; managing a KB stays an admin
  RBAC concern.
- **Scope resolver** (`src/services/access/scope.py`):

  ```python
  @dataclass(frozen=True)
  class RetrievalScope:
      kb_ids: frozenset[str] | None   # None = unrestricted (ALL)
      def restrict(self, requested: Iterable[str] | None) -> ScopeDecision: ...
  # ScopeDecision = (permitted: list[str] | None, dropped: list[str])

  def resolve_scope(principal, session, settings) -> RetrievalScope
  ```

  - In `disabled` mode it returns `ALL`.
  - For admins with `AUTH__ADMIN_BYPASS_RETRIEVAL=true` (default **false**) it
    returns `ALL`. This is **the single place** the admin bypass is checked,
    avoiding OWUI's inconsistency.
  - Otherwise it returns the KB IDs with a grant matching `everyone`, the
    principal's `subject`, any of its `groups`, or any of its `raw_roles`. It
    runs one indexed query per request.
  - `AUTH__UNGRANTED_KB_ACCESS=deny` is the default. The alternative, `allow`,
    lets any querier read a KB that has **zero** grants. That gives existing
    deployments a gradual path, with the strict setting documented as the
    recommended end state.
- **`ScopeDep`** in `dependencies.py` resolves the scope once per request,
  from `PrincipalDep` and `SessionDep`.
- **Call sites.** Each gets an *optional* `scope` argument, where `None` means
  `ALL`, so current callers and tests are unaffected.

  | Path | Change |
  |------|--------|
  | `services/applications/answer.py` `retrieve_for_application` (playground + `/v1`) | The KB IDs become the application's KBs ∩ the scope. If the application has no KBs, keep the existing `NO_KNOWLEDGE_BASE_MESSAGE`. If it has KBs but none are permitted, return a new `NO_PERMITTED_KNOWLEDGE_MESSAGE`. **Fail closed** either way. `dropped` is returned for S4. |
  | `routers/ask.py` `_prepare_chunks_and_sources` (`/ask`, `/stream`) | Pass `knowledge_base_ids=permitted` when the scope isn't `ALL`. An empty scope goes down the existing "no results" path. |
  | `routers/hybrid_search.py` | Same as `/ask`. |
  | Agentic (`/ask-agentic`) | `get_agentic_rag_service` is **already per-request**, and `AgenticRAGService` builds its retriever tool in `__init__` (`agentic_rag.py:88`). Add `knowledge_base_ids` to `GraphConfig`, and pass it through to `create_retriever_tool(..., knowledge_base_ids=...)` and `search_unified`. The graph nodes don't change. |

- **Defense in depth at the chokepoint.** `OpenSearchClient.search_unified` and
  `search_chunks_vector` currently treat `knowledge_base_ids=[]` as
  "unscoped", because they only check truthiness. That's the trap
  `answer.py:66-69` warns about. Change it to **`None` = unscoped, `[]` =
  match nothing (return empty results)**. Checked: no existing test pins the
  `[]` behaviour (`test_opensearch_kb_scoping.py` only uses `[]` for the
  `knn_k` filter-clause argument), and no current caller passes `[]`,
  because `answer.py` returns early.
- **Cache isolation.** `CacheClient._generate_cache_key` adds
  `"scope": sorted(permitted)` **only when the scope isn't `ALL`**, so default
  mode produces byte-identical keys and there's no cache flush.
- **Grant management:**
  - `GET` and `PUT /api/v1/knowledge-bases/{kb_id}/grants`. The PUT replaces
    the whole set. Both are admin-only.
  - A "Access" panel on the KB detail page lists the grants and edits them as
    group, role or subject entries.
  - A read-only "effective access" preview, `GET …/grants/check?group=…`, lets
    admins verify a mapping.

### 2. Data model changes (new table only)

```
knowledge_base_grants
  id                GUID  PK
  knowledge_base_id GUID  FK knowledge_bases.id ON DELETE CASCADE, indexed
  principal_type    String NOT NULL   -- group | role | subject | everyone
  principal_value   String NOT NULL   -- claim value; "*" for everyone
  permission        String NOT NULL DEFAULT 'read'
  created_at        DateTime
  created_by        String NULL       -- principal.subject of the admin
  UNIQUE (knowledge_base_id, principal_type, principal_value, permission)
  INDEX  (principal_type, principal_value)
```

This is a new model in `src/models/access.py`, imported in
`src/models/__init__.py` (required for `create_all`), with a repository in
`src/repositories/grants.py`.

### 3. Backward compatibility

- In `disabled` mode the scope is `ALL`, the call sites behave exactly as
  today, and cache keys are unchanged.
- The table is created empty on existing deployments.
- The one semantic change that ships regardless of mode is `[]` meaning
  "match nothing". It's unreachable by current callers and makes the unsafe
  state impossible.
- **Operator-visible behaviour when auth is enabled:**
  - Legacy global endpoints stop returning KBs the caller isn't granted.
  - With the default `UNGRANTED_KB_ACCESS=deny`, a freshly-enabled deployment
    returns nothing until grants are created. The README rollout guide says
    so, and recommends `allow` during migration.

### 4. Rollout order (commits)

1. `[]` means match-nothing in the OpenSearch client, plus tests. Standalone
   hardening.
2. `KnowledgeBaseGrant` model, repository, and the grants API.
3. `RetrievalScope`, `resolve_scope`, `ScopeDep` and settings, with unit tests.
4. Wire the application path (`answer.py` + both routers).
5. Wire `/ask`, `/stream` and `/hybrid-search`.
6. Wire the agentic path via `GraphConfig`.
7. Cache-key scope isolation.
8. The frontend Access panel.
9. README "Knowledge-base permissions" and a rollout guide; `.env.example`.

### 5. Test plan

- **Resolver** (SQLite): grants by group, role, subject and everyone; no
  grants under `deny` and `allow`; the admin bypass on and off; disabled mode
  returns `ALL`.
- **Intersection:** application KBs {A, B, C} ∩ permitted {B} gives
  permitted = {B} and dropped = {A, C}. Permitted = ∅ gives the fail-closed
  message and **no OpenSearch call**, asserted on the mock.
- **Per path:** the `search_unified` mock asserts the `knowledge_base_ids`
  argument for the application, `/ask`, `/stream`, `/hybrid-search` and the
  agentic retriever tool.
- **Invariant test:** with auth enabled and a scoped principal, *every*
  OpenSearch search call made during each answer path receives a non-`None`
  `knowledge_base_ids`. This guards against a future path forgetting the
  scope.
- **Cache:** two principals with different scopes get different keys; the
  disabled-mode key equals today's key (pinned hash).
- **OpenSearch client:** `[]` returns empty results without calling the
  cluster; `None` stays unscoped.
- **Grants API CRUD**, admin only.
- **Existing suite: unmodified.** `test_application_answer.py` calls
  `retrieve_for_application` without `scope`, so its behaviour is identical.

### 6. Rollback

- Set `AUTH__MODE=disabled`: the scope becomes `ALL` immediately. Grants
  remain in the table, inert.
- Setting `AUTH__UNGRANTED_KB_ACCESS=allow` is a softer mitigation if grants
  were misconfigured.
- For a code rollback, revert the commits. The table can be left in place (it
  is unused) or dropped manually with `DROP TABLE knowledge_base_grants`; a
  documented SQL snippet will be provided.

---

## S4. Structured audit log

### 1. Design

- **Two event sources:**
  1. **Query events**, recorded explicitly on every answer path. These carry
     retrieval facts that a middleware can't see.
  2. **Admin mutations**, recorded by a small ASGI middleware for
     `POST/PUT/PATCH/DELETE` under `/api/v1` (excluding the query routes).
     It captures the route template, status code and principal (from
     `request.state.principal`). The resource ID is taken from path params.
     Using a middleware avoids editing about 20 CRUD handlers and captures the
     real status code.
- **`AuditRecorder`** (`src/services/audit/recorder.py`) is built by a new
  `make_audit_recorder()` factory. It's stored on `app.state.audit_recorder`
  and exposed as `AuditDep`, following the CLAUDE.md pattern. It writes with
  its **own short-lived session**, not the request session, so a request
  rollback doesn't erase the audit row and an audit failure doesn't roll back
  the request. The recorder is synchronous, one INSERT per event.
  - `AUDIT__LEVEL=off` (the default) gives a `NoopAuditRecorder` that never
    touches the DB.
  - `metadata` stores IDs, hashes and decisions, but **no query or answer
    text**.
  - `full` stores `metadata` plus the query and answer text.
  - `AUDIT__FAIL_MODE=log` (default) logs an ERROR and continues when the
    insert fails. `deny` returns 503 instead of the answer. For streaming,
    `deny` can only fail the final event, which is documented.
- **An `AuditContext` dependency** gathers the request ID, source IP, user
  agent, principal, API-key ID and application ID. It's passed to the
  recorder along with the retrieval result.
- **Query event integration.** One helper, `record_query_event(recorder, ctx,
  action, scope_decision, hits, answer, model, cache_hit, outcome,
  latency_ms)`, is called from:
  - `answer.py` callers: `routers/applications.py` (playground) and
    `routers/chat_completions.py` (`/v1`, streaming and non-streaming);
  - `routers/ask.py` (`/ask` and `/stream`, **including cache hits**, where
    `cache_hit=true` and the cached sources are recorded);
  - `routers/agentic_ask.py`;
  - `routers/hybrid_search.py` (`action=query.search`, no answer).

  For streaming, all values are captured before the `StreamingResponse` is
  created, and the event is recorded when the generator finishes, including
  `outcome=error` on exception. This mirrors the "no ORM objects in the
  generator" rule already documented at `chat_completions.py:120`.
- **Retrieved chunk IDs.** Every search path already sets `chunk["chunk_id"] =
  hit["_id"]` (`opensearch/client.py:194,282,345,457`). `answer.py` exposes the
  raw `hits`. The legacy `_prepare_chunks_and_sources` currently drops
  `chunk_id` and `knowledge_base_id`, so it gains them in the chunk dict. That
  doesn't change the prompt: the prompt builder reads `chunk_text`, `title`
  and `section_title`.
- **Auditor API** (`routers/audit.py`, auditor role, or admin if
  `AUTH__ADMIN_CAN_READ_AUDIT`):
  - `GET /api/v1/audit/events`, with filters `actor`, `action`,
    `application_id`, `knowledge_base_id` (JSON containment over
    `permitted_kb_ids`, done in Python for SQLite portability or with a
    Postgres `@>` fast path), `outcome`, `since` and `until`, plus
    keyset pagination.
  - `GET /api/v1/audit/events/{id}`.
  - `GET /api/v1/audit/export?format=ndjson|csv&since=&until=`, streamed.

  There are **no update or delete endpoints**.
- **Frontend.** An "Audit" page with a filterable table, an event detail
  drawer and an export button. It's shown only when `/system/security`
  reports `audit_level != off`.
- **Retention:** `AUDIT__RETENTION_DAYS=0`, where 0 means keep forever. Purging
  is an explicit operator command, `make audit-purge DAYS=365` →
  `python -m src.cli.audit_purge`. There's deliberately no HTTP endpoint for
  it.
- **Deferred (documented follow-up):** a tamper-evidence hash chain, which
  needs serialized inserts (a Postgres advisory lock), and an append-only
  Postgres role recipe (`REVOKE UPDATE, DELETE ON audit_events`). The recipe
  goes in the docs in v1; the chain comes later.

### 2. Data model changes (new table only)

```
audit_events
  id                 GUID PK
  occurred_at        DateTime NOT NULL (UTC, server-set), indexed
  request_id         String, indexed
  action             String NOT NULL, indexed   -- query.answer | query.search | admin.mutation
  outcome            String NOT NULL             -- ok | denied | error
  status_code        Integer NULL
  actor_subject      String, indexed
  actor_roles        JSON
  actor_groups       JSON
  auth_method        String
  api_key_id         GUID NULL
  application_id     GUID NULL, indexed
  source_ip          String NULL
  user_agent         String NULL
  http_method        String NULL
  route              String NULL                 -- route template, e.g. /api/v1/knowledge-bases/{kb_id}
  resource_type      String NULL
  resource_id        String NULL
  requested_kb_ids   JSON NULL
  permitted_kb_ids   JSON NULL
  dropped_kb_ids     JSON NULL
  retrieved          JSON NULL                   -- [{chunk_id, document_id, knowledge_base_id, score}]
  cache_hit          Boolean NULL
  model              String NULL
  query_text         Text NULL                   -- level=full only
  answer_text        Text NULL                   -- level=full only
  query_sha256       String NULL
  answer_sha256      String NULL
  latency_ms         Integer NULL
  error_code         String NULL
```

It's a new model in `src/models/audit.py`, imported in `models/__init__.py`.
The `*_kb_ids` fields exist before S3 lands; until then they're filled with
`requested` = `permitted`, which is all KBs searched, and `dropped` = `[]`.

### 3. Backward compatibility

- With `AUDIT__LEVEL=off` the no-op recorder is used: no DB writes, no
  middleware effect, no behaviour change.
- The table is created empty.
- Answer-path functions gain only optional parameters.
- Langfuse is untouched and remains the optional observability layer. The
  request ID is added to trace metadata so records can be joined.

### 4. Rollout order (commits)

1. `AuditEvent` model and `AuditSettings`; `make_audit_recorder` with no-op
   and DB recorders; the lifespan wiring; the `tests/api/conftest.py` patch.
2. `AuditContext` dependency and the `record_query_event` helper.
3. Wire the application path (playground + `/v1`, both streaming modes).
4. Wire `/ask` and `/stream` (including cache hits), agentic and hybrid search.
5. The admin-mutation middleware.
6. The auditor API (list, detail, export).
7. The frontend Audit page.
8. The purge CLI, the `make audit-purge` target, and the retention docs.
9. README "Audit logging" (including the append-only DB role recipe) and
   `.env.example`.

### 5. Test plan

- **Recorder** (SQLite):
  - at `metadata`, text fields are NULL and hashes are set;
  - at `full`, text fields are set;
  - at `off`, no rows are written;
  - with the insert failing, `log` doesn't block and `deny` returns 503.
- **Per path:** after a call, exactly one event exists with the correct
  `action`, actor, application, `retrieved` chunk IDs from the mocked hits,
  and `cache_hit`. For streaming, the event appears after the stream is
  consumed and `outcome=error` is recorded when the generator raises.
- **S3 integration** (once both have landed): `dropped_kb_ids` is populated
  when application KBs are filtered.
- **Admin middleware:** KB create, delete and grants update each produce an
  `admin.mutation` event with the route template and status code. A GET
  produces none.
- **Auditor API:** filters, pagination and export formats. Querier → 403.
  Auditor → 200. Admin → 200 or 403 depending on
  `AUTH__ADMIN_CAN_READ_AUDIT`.
- **Existing suite: unmodified.** The level defaults to `off`. The only
  fixture change is the new `make_audit_recorder` patch in
  `tests/api/conftest.py`.

### 6. Rollback

- Set `AUDIT__LEVEL=off` and restart. Recording stops immediately and
  existing rows are kept for the compliance record.
- For a code rollback, revert the commits. **Don't** drop `audit_events` on
  rollback; the rows may be subject to retention obligations. A documented
  export-then-drop procedure covers that if it's ever needed.

### As built (differences from this plan)

- **Audit status** is at `GET /api/v1/audit/status` (auditor role), not an
  `audit_level` field on `/system/security`. That endpoint is admin-only, so
  a non-admin auditor couldn't have read it. It also means the existing
  exact-match test on `/system/security` stays unchanged.
- **Three extra actions:** `access.denied` (any other 401/403, recorded by the
  middleware), `audit.export` (recorded *before* data is sent, so `deny` can
  refuse an unauditable export) and `audit.purge` (written in the same
  transaction as the delete, even at level `off`).
- **A `details` JSON column** holds action-specific extras: path parameters,
  cached source titles on cache hits, agentic retrieval attempts, export
  filters and purge counts.
- **Cache hits** record `retrieved = NULL` (no retrieval ran for that
  request) and the cited titles in `details`, rather than claiming chunks.
- **The Audit nav entry** is shown to callers holding the auditor role. The
  page itself says when recording is off, instead of the entry being hidden.
- **Admin-mutation audit failures are always log-only.** The change has
  already committed and the response has been sent.
- **The Langfuse request ID** is added on `/ask` and `/stream`, which use
  `RAGTracer`. The agentic service builds its own traces and was not changed.

---

## S5. Hardened compose overlay

### 1. Design

- **A new `compose.hardened.yml`** that uses `ports: !override []` (the same
  `!override` mechanism `compose.localports.yml` already uses) to **remove
  host port publishing** from these services, which stay reachable only on
  `rag-network`:
  - `postgres`
  - `redis`
  - `opensearch` (9200/9600)
  - `opensearch-dashboards`
  - `ollama`
  - `clickhouse`
  - `langfuse-postgres`
  - `langfuse-redis`
  - `langfuse-minio`
  - `langfuse-worker`
- `api` and `frontend` are rebound to `127.0.0.1`, so a buyer's reverse proxy
  or ingress is the only way in. `langfuse-web` is rebound to `127.0.0.1` as
  well.
- **A new `make start-hardened` target:** `docker compose -f compose.yml -f
  compose.hardened.yml up --build -d`. It's additive to the Makefile.
- **Docs.** Pair it with `AUTH__MODE`, and state plainly that the
  **OpenSearch security plugin is still disabled** inside the network.
  Enabling it (TLS + credentials in `OpenSearchClient`) is a documented
  follow-up. Network isolation is the v1 control.

### 2. Data model changes

None.

### 3. Backward compatibility

`compose.yml` is untouched and `make start` is unchanged. The overlay is
opt-in.

### 4. Rollout order

A single commit: the overlay file, the Makefile target, and a README
"Production hardening" section. It can land alongside any security-track
feature.

### 5. Test plan

- `docker compose -f compose.yml -f compose.hardened.yml config` validates.
- Bring the stack up hardened, then confirm from the host that:
  - `curl localhost:9200` and a TCP connection to `localhost:5432` **fail**;
  - `curl 127.0.0.1:8000/api/v1/health` succeeds;
  - `make health` reports the API as up and OpenSearch and Ollama as not
    responding, which is expected and documented.
- `make test` is unaffected.

### 6. Rollback

Start without the overlay (`make start`).

---

## S3a. Document-level ACL tags *(Important; later, after S3 and S4)*

**Design, summarized.** This is planned in full only when S3 and S4 have
shipped.

- **A new `document_acl_tags` table** (`document_id` FK cascade, `tag`
  String, unique pair), managed via
  `PUT /api/v1/documents/{id}/acl-tags`. Tags are claim values such as
  groups.
- **An additive `PUT _mapping`** adds an `acl_tags` keyword field to the chunk
  index. The mapping is strict, but adding fields is allowed.
- **Ingestion writes `acl_tags` onto every chunk**, and re-tagging a document
  updates its chunks via `update_by_query`.
- **The scope filter becomes:** `knowledge_base_id ∈ permitted AND (acl_tags ∈
  principal.groups ∪ raw_roles ∪ {subject} OR NOT exists(acl_tags))`.
  Untagged documents fall back to KB-level access, which is backward
  compatible.
- **Precondition decision: the kNN engine.** The index uses **nmslib**, so
  filters are post-filters, and the extra selectivity makes the 10×
  oversampling unreliable (`opensearch/client.py:16-33`). The recommended fix
  is switching to the `lucene` or `faiss` engine, which requires creating a
  new index and reindexing. That's a separate, explicitly approved step with
  its own rollback (alias swap).
- **Audit:** events add `acl_filter_applied: true` and the tag set used.

---

# Hardware/Backend track ⚙️

*This track is independent of the security track. It can run in parallel with
a separate owner, and its commits don't touch auth, grants or audit code.*

## H1. OpenAI-compatible backend verification (vLLM, TGI, llama.cpp-server)

### 1. Design

- **Verify** that `OpenAICompatibleLLMProvider` works through the existing
  `LLM__PROVIDER=openai-compatible` (alias `vllm`) setting against:
  1. **Ollama's own `/v1` endpoint.** This works on any machine, including
     this Mac, and is the cheapest test of the provider.
  2. **llama.cpp `llama-server`**, CPU and GGUF. It runs in Docker on this
     Mac.
  3. **vLLM** and **TGI**. These need an **NVIDIA GPU host** and can't be
     verified on this development machine. They're covered by contract tests
     built from recorded response shapes, plus a manual checklist to run on a
     GPU host.
- **Small provider fixes** found in the Phase 2b code read. Each applies only
  on the OpenAI-compatible path; the Ollama default is untouched.
  - **Send the system prompt as a `system` role message**, instead of
    folding it into the user message. This needs `RAGPromptBuilder` to expose
    system and user parts; the existing `create_rag_prompt` is kept for the
    Ollama path.
  - **`LLM__MAX_TOKENS`** (default `1024`) is sent as `max_tokens`. This
    avoids TGI-style tiny default limits.
  - **Health check:** if `GET /models` returns 404, fall back to reporting
    "reachable (no /models)" rather than unhealthy.
  - **Streaming:** surface SSE `error` payloads as an exception instead of
    silently yielding an empty answer.
- **Deliverables:**
  - `docs/deployment/inference-backends.md`, with one section per backend:
    image, launch flags (e.g. vLLM `--tensor-parallel-size`, `--quantization
    awq`), the `LLM__*` values, and known quirks;
  - example overlays `compose.vllm.yml` and `compose.llamacpp.yml`. These
    are opt-in; `compose.yml` is untouched;
  - `.env.example` snippets;
  - **H4 content:** GGUF via Ollama (library tags and Modelfile import), and
    AWQ/GPTQ/FP8 via vLLM.

### 2. Data model changes

None.

### 3. Backward compatibility

The default `LLM__PROVIDER=ollama` is untouched, and every fix sits inside
`OpenAICompatibleLLMProvider`.

**Existing test to check:**
`tests/unit/services/providers/test_providers.py` doesn't currently assert the
message shape, so the system-role change shouldn't require edits. If it does,
the change is intentional and will be explained.

### 4. Rollout order

1. Provider fixes, with contract tests using `httpx.MockTransport` fixtures
   that mimic vLLM, TGI and llama.cpp responses (including an SSE error and a
   404 on `/models`).
2. A local smoke test against Ollama `/v1` and llama.cpp `llama-server`.
3. The docs, example overlays, `.env.example` and README pointer.
4. *(On a GPU host, not this machine)* the manual vLLM and TGI checklist,
   with results appended to the doc.

### 5. Test plan

- Contract tests per backend shape: non-streaming, streaming, usage parsing,
  system message present, `max_tokens` sent, `/models` 404 handling, SSE
  error surfacing.
- Manual smoke tests: the playground and `/v1/chat/completions` with
  `LLM__PROVIDER=openai-compatible` pointed at Ollama `/v1`, then at
  llama.cpp.

  **Note:** until H2 lands, `/ask`, `/stream` and `/ask-agentic` still use
  Ollama directly. That's expected and documented.
- The existing suite is unmodified (see the note in section 3).

### 6. Rollback

Set `LLM__PROVIDER=ollama`. For a code rollback, revert. Nothing persistent is
involved.

---

## H3. GPU compose overlays and VRAM display

### 1. Design

- **Opt-in overlays:**
  - **`compose.gpu.nvidia.yml`** reserves GPUs on the **`ollama`** service:
    `deploy.resources.reservations.devices` with
    `driver: ${OLLAMA_GPU_DRIVER:-nvidia}`, `count: ${OLLAMA_GPU_COUNT:-all}`
    and `capabilities: [gpu]`. It requires the NVIDIA Container Toolkit.
  - **`compose.gpu.amd.yml`** passes through `/dev/kfd` and `/dev/dri` and
    uses the `ollama/ollama:0.11.2-rocm` image tag.
  - **`make start-gpu`** takes `GPU=nvidia|amd`.
  - **Docs:** macOS Docker containers can't reach the Apple GPU, so Mac users
    should run native Ollama and point `OLLAMA_HOST`, `LLM__ENDPOINT` and
    `EMBEDDING__ENDPOINT` at `host.docker.internal:11434`.
- **Fix `/api/v1/system/gpu`.** Keep the existing `nvidia-smi` probe, but
  relabel it `source: "api-container"`. Add an **Ollama residency probe**:
  `GET {ollama}/api/ps` returns each loaded model's `size` and `size_vram`,
  from which we compute the percentage on GPU. The endpoint now tells the
  truth that matters, for example "llama3.2:1b: 100% GPU, 1.6 GB VRAM" or
  "0% GPU (CPU)".
- **Additive schema.** `GPUResponse` gains:
  - `source: str | None`
  - `inference: list[ModelResidency]`, where `ModelResidency` has `name`,
    `size_bytes`, `vram_bytes`, `gpu_percent` and `processor`
    (`gpu`/`cpu`/`split`)
- **The System page** renders a "Model residency" table under the existing GPU
  card.

### 2. Data model changes

None.

### 3. Backward compatibility

The overlays are opt-in files. The `GPUResponse` fields are optional
additions, so `available` and `gpus` keep their meaning. The existing
`test_gpu_endpoint_never_fabricates` only asserts that `available` and `gpus`
are present with the right types, so it stays unmodified. The Ollama probe is
wrapped so that an unreachable Ollama gives an empty `inference` list, never
an error.

### 4. Rollout order

1. The overlays, the Makefile target, and the docs.
2. The Ollama `/api/ps` probe and `GPUResponse` fields, with tests.
3. The System page residency table.

### 5. Test plan

- Unit-test the probe with a mocked `/api/ps` payload: full GPU, CPU-only,
  split, and Ollama down.
- `docker compose -f compose.yml -f compose.gpu.nvidia.yml config` validates.
  A real GPU run happens on a GPU host.
- `npm run build`.
- The existing suite is unmodified.

### 6. Rollback

Start without the overlay. For a code rollback, revert. Nothing persistent is
involved.

---

## H5. Embedding dimension from config

### 1. Design

- **`index_config_hybrid.py`** gains `build_chunk_mapping(dimension: int)`.
  `DOCUMENT_CHUNKS_MAPPING = build_chunk_mapping(1024)` is kept as a
  module constant so existing imports, including
  `tests/unit/test_wiring_regressions.py::TestIndexPayloadMatchesMapping`,
  keep working unmodified.
- **Single source of truth: `EMBEDDING__DIMENSION`.**
  `OPENSEARCH__VECTOR_DIMENSION` is currently unused. It becomes deprecated,
  and startup logs a warning if it's set and differs.
- **`setup_indices`** creates new indices with the configured dimension. If
  the index already exists, startup reads its mapped dimension. On a mismatch
  it logs an ERROR and sets a flag, and ingestion endpoints return **409**
  with a clear message ("index dimension 1024 ≠ configured 768; reindex
  required"). The API and search stay up.

### 2. Data model changes

None. A dimension change on existing data is a reindex, now made explicit
rather than silent bulk failures.

### 3. Backward compatibility

The default dimension is 1024, so new indices are identical to today's and
existing indices match.

### 4. Rollout order

A single commit: the builder, the guard, the 409 path, tests and docs.

### 5. Test plan

- The builder output with 768 has `dimension: 768`.
- With a mocked existing index at 1024 and config at 768: the guard is set,
  upload returns 409, and search still works.
- With matching dimensions, there's no guard.
- The existing suite is unmodified.

### 6. Rollback

Revert. Behaviour returns to the hardcoded 1024.

---

## H2. Route every LLM path through `LLMProvider`

### 1. Design

- **`LLMProvider` gains a non-abstract method,**
  `get_langchain_model(model=None, temperature=0.0, **kw)`. It's non-abstract
  so the interface isn't broken for any subclass.
  - `OllamaClient` already implements it and returns a `ChatOllama`.
  - `OpenAICompatibleLLMProvider` returns
    `langchain_openai.ChatOpenAI(base_url=endpoint, api_key=key or
    "not-needed", model=...)`.
  - The structured-output method is configurable:
    `LLM__STRUCTURED_OUTPUT_METHOD=json_schema|json_mode|function_calling`,
    default `json_mode`, which is the most widely supported by vLLM, TGI and
    llama.cpp.
- **New dependency: `langchain-openai`.** Guard against accidental egress:
  - `ChatOpenAI` always receives an explicit local `base_url`, and the factory
    refuses an empty endpoint.
  - A test blocks sockets (monkeypatched `socket.connect`) and runs
    `invoke`/`with_structured_output` against an `httpx` mock, proving no
    tokenizer download (`tiktoken`) or other call happens on the request path.
- **Legacy `/ask` and `/stream`** switch from `OllamaDep` to `LLMProviderDep`.
  `OllamaClient` already implements the same `generate_rag_answer` and
  `generate_rag_answer_stream` contract with the same chunk shape. If the
  caller doesn't send a model, the provider's `default_model` is used. Today
  `AskRequest.model` defaults to `"llama3.2:1b"`, which is kept for Ollama.
- **Agentic graph.** `Context` gains an optional `llm_provider` field and a
  property, `chat_model_source`, that returns `llm_provider` if it's set and
  otherwise `ollama_client`. The four nodes call
  `runtime.context.chat_model_source.get_langchain_model(...)`.
  `make_agentic_rag_service` and `get_agentic_rag_service` pass
  `LLMProviderDep`. Because of the fallback, **existing node fixtures (which
  only set `ollama_client`) keep working unmodified.**
- **`/api/v1/health`** adds a `"llm"` entry reporting the configured
  provider's health. The existing `"ollama"` entry is kept as-is when Ollama
  is in use; `test_ping.py` only asserts that `services` exists.

### 2. Data model changes

None.

### 3. Backward compatibility

With `LLM__PROVIDER=ollama` (the default), `make_llm_provider` returns an
`OllamaClient`, so `/ask`, `/stream` and the agentic nodes call the same
methods on an equivalent object. `tests/api/conftest.py` already patches
`make_ollama_client` and `make_llm_provider` with the same mock factory.

**Check:** `test_wiring_regressions.py::TestAgenticServiceWiring` pins the
factory's signature. Adding an *optional* `llm_provider` parameter keeps it
passing. If any assertion pins the exact parameter list, updating it is an
intended signature change, and the commit message will say so.

### 4. Rollout order

1. The `get_langchain_model` method on the interface and its
   `OpenAICompatibleLLMProvider` implementation, plus `uv add
   langchain-openai`, with a no-egress test.
2. Switch `/ask` and `/stream` to `LLMProviderDep`.
3. `Context.llm_provider`, `chat_model_source`, the four nodes, and the
   factory/dependency.
4. The health endpoint `"llm"` entry.
5. A new wiring-regression test pinning that the agentic nodes use
   `chat_model_source`.
6. Docs: "all endpoints honour `LLM__PROVIDER`".

### 5. Test plan

- **Unit:** each node with a `Context` holding only `ollama_client` (the
  existing fixtures, unmodified) and with an `llm_provider` mock. Assert the
  provider is used when it's present.
- **API:** `/ask` with `make_llm_provider` returning a distinct mock. Assert
  that mock is called rather than the Ollama one.
- **No-egress** test for `ChatOpenAI`.
- **Manual:** `LLM__PROVIDER=openai-compatible` → Ollama `/v1`; exercise
  `/ask`, `/stream` and `/ask-agentic`. After this change, the agentic
  guardrail and grader use `with_structured_output` over the OpenAI-compatible
  path.
- **Existing suite: unmodified** (see the check in section 3).

### 6. Rollback

Set `LLM__PROVIDER=ollama`, which is identical in behaviour to today. For a
code rollback, revert. Nothing persistent is involved.

---

## H2a. Honour per-application provider and endpoint

### 1. Design

- **A new `ProviderRegistry`** (`services/providers/registry.py`) is an
  app-state service built by `make_provider_registry()`, following the
  CLAUDE.md pattern. `get_llm(provider_type, endpoint, model, api_key)`
  returns a cached `LLMProvider`, with an LRU keyed by `(type, endpoint,
  key-fingerprint)`. It reuses a refactored
  `make_llm_provider_from(type, endpoint, model, api_key, timeout)` that the
  existing `make_llm_provider(settings)` delegates to, so its signature is
  unchanged.
- **Resolution in `answer.py`:** if `application.model_configuration` has a
  non-null `llm_endpoint`, use
  `registry.get_llm(cfg.llm_provider, cfg.llm_endpoint, cfg.llm_model, …)`.
  Otherwise use the global provider, which is today's behaviour.
- **Secrets.** `ModelProvider.api_key_ref` is currently passed straight
  through as the key (`routers/providers.py:158`), so the secret is stored in
  the DB. The new rule: a value of the form `env:VAR_NAME` is resolved from
  the process environment, and any other value is used literally, which is
  backward compatible. The docs recommend `env:`. The same resolution applies
  to per-application endpoints.
- **SSRF control (pairs with S2).** An optional allowlist,
  `LLM__ALLOWED_ENDPOINT_HOSTS`, defaults to empty, meaning any host (today's
  behaviour). When set, per-application endpoints and provider `/test` are
  restricted to those hosts.
- **Observation, out of scope here:** `RAGApplication.rag_strategy="agentic"`
  is stored but `answer.py` always runs the traditional pipeline. This gets
  flagged for a later decision.

### 2. Data model changes

None. The existing `model_configurations.llm_provider` and `llm_endpoint`
columns are used.

### 3. Backward compatibility

Existing rows default to `llm_provider="ollama"` and `llm_endpoint=NULL`, so
the global provider is used and behaviour is unchanged. The existing
`test_application_ask_respects_configuration` uses a null endpoint and stays
unmodified.

### 4. Rollout order

1. The `make_llm_provider_from` refactor, with `make_llm_provider` delegating
   to it (signature unchanged).
2. `ProviderRegistry`, its factory, lifespan and dependency, and the
   `tests/api/conftest.py` patch.
3. Resolution in `answer.py` (and therefore in both routers).
4. `env:` secret resolution and the optional host allowlist.
5. A UI hint on the application form: "endpoint set → this application uses
   its own backend".

### 5. Test plan

- **SQLite domain test:** an application whose configuration has an
  OpenAI-compatible endpoint, with the registry returning a distinct mock.
  Assert it's used, and that the global mock isn't.
- A null endpoint uses the global provider.
- Registry caching (same key gives the same instance).
- `env:` resolution, including a missing environment variable, which gives a
  clear 500 naming the variable.
- The allowlist rejects a non-listed host.
- The existing suite is unmodified.

### 6. Rollback

Clear `llm_endpoint` on the affected applications (a UI edit) to return them
to the global provider. For a code rollback, revert. Nothing persistent was
added.

---

## Explicitly not planned (from Phase 3)

| Item | Reason |
|------|--------|
| **H4 as code** | GGUF works today through Ollama, and AWQ/GPTQ come with H1. It's documentation only (in H1). Displaying the quantization level is an optional nice-to-have. |
| **H6** multi-connection catalogue / vector-store interface | Large, and there's no current buyer need: H2a covers per-application backends and OpenSearch is a product choice. |
| **H7** in-process llama-cpp-python / raw Triton adapters | Only on explicit buyer demand, because they put native builds and model memory inside the API process. |
| **F2** end-user chat UI | Buyers embed us behind their own UI via `/v1/chat/completions`. |
| **F3** plugin/tool system | In-process arbitrary Python conflicts with the regulated-buyer security story. If ever built, it would be out-of-process (MCP), role-gated and audited. |

## Approval checklist for you

- [ ] Security track order and scope (S1 → S2/S2a/F1 → S3 → S4, S5 alongside,
      S3a later)
- [ ] The role names `admin` / `querier` / `auditor` and the route policy
      matrix in S2
- [ ] `AUTH__UNGRANTED_KB_ACCESS` defaulting to `deny` (strict, with `allow`
      available for migration)
- [ ] Audit default `AUDIT__FAIL_MODE=log` (with `deny` for strict
      deployments)
- [ ] The one mode-independent semantic change: `knowledge_base_ids=[]` means
      match nothing (S3 commit 1)
- [ ] New dependencies: `pyjwt[crypto]` (S1), `langchain-openai` (H2), and
      `oidc-client-ts` in the frontend (F1)
- [ ] Hardware track order (H1 → H3 → H5 → H2 → H2a), with vLLM/TGI final
      verification needing a GPU host that isn't this machine
