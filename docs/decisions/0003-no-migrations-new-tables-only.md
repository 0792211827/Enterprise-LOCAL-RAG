# ADR-0003: No Alembic yet; new persistent structures are new tables

- **Status:** Accepted; revisit when a change to an existing table is unavoidable.
- **Context:** Tables are created with `Base.metadata.create_all` (`db/interfaces/postgresql.py`). Alembic is installed but unused. `create_all` never alters an existing table.
- **Decision:** Every feature so far (grants, audit, and so on) added **new tables only**, using portable column types so SQLite-based tests work. New models must be imported in `src/models/__init__.py`.
- **Consequences:** adding a column to an existing table (for example an API-key `expires_at`) needs either a side table or an explicit decision to introduce Alembic with a baseline migration. That decision is called out in roadmap Phase 7.
- **Rollback rule:** never drop `audit_events` on rollback; rows may be under retention obligations.
