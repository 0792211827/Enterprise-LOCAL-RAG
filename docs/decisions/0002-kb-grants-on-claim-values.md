# ADR-0002: Knowledge-base grants key on IdP claim values; no local users/groups table

- **Status:** Accepted (implemented as S3).
- **Context:** Retrieval must be limited per caller, but the platform does not own users (ADR-0001).
- **Decision:** `knowledge_base_grants` rows carry `principal_type` (`group` | `role` | `subject` | `everyone`) and a claim value. A single resolver, `services/access/scope.py::resolve_scope`, decides the readable KB set; every search path intersects its request with it. An empty scope matches nothing (never "everything"). Defaults: `AUTH__UNGRANTED_KB_ACCESS=deny`, `AUTH__ADMIN_BYPASS_RETRIEVAL=false`. A valid application API key without a forwarded end user is unrestricted at the scope layer and limited by the application's attached KBs. Cache keys include a scope digest.
- **Consequences:** grants survive IdP changes without a sync job, and admins can preview a caller with `GET /knowledge-bases/access-check`. Grants are read-only retrieval permission (no per-KB editor/admin role). Document-level ACLs are out of scope (see ADR-0004 and roadmap Phase 12).
