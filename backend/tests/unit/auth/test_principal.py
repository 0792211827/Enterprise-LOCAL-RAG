"""Principal construction and IdP claim mapping."""

import pytest
from src.auth import ANONYMOUS_SUBJECT, Principal, Role
from src.auth.principal import as_values, claim_at, map_roles
from src.config import AuthSettings


def _settings(**overrides) -> AuthSettings:
    base = dict(admin_roles="rag-admin", query_roles="rag-user", auditor_roles="rag-auditor")
    return AuthSettings(**{**base, **overrides})


class TestAnonymous:
    def test_holds_every_role(self):
        principal = Principal.anonymous()
        assert principal.roles == frozenset(Role)
        assert principal.auth_method == "disabled"

    def test_subject_matches_legacy_trace_value(self):
        assert Principal.anonymous().subject == ANONYMOUS_SUBJECT == "api_user"


class TestClaimAt:
    def test_top_level(self):
        assert claim_at({"sub": "u1"}, "sub") == "u1"

    def test_dotted_path(self):
        assert claim_at({"realm_access": {"roles": ["a"]}}, "realm_access.roles") == ["a"]

    @pytest.mark.parametrize("path", ["missing", "realm_access.missing", "sub.deeper"])
    def test_absent_returns_none(self, path):
        assert claim_at({"sub": "u1", "realm_access": {}}, path) is None


class TestAsValues:
    def test_list(self):
        assert as_values(["a", " b ", ""]) == frozenset({"a", "b"})

    def test_delimited_string(self):
        assert as_values("a, b,,c") == frozenset({"a", "b", "c"})

    def test_custom_separator(self):
        assert as_values("a;b", separator=";") == frozenset({"a", "b"})

    def test_none_and_scalar(self):
        assert as_values(None) == frozenset()
        assert as_values(42) == frozenset({"42"})


class TestMapRoles:
    def test_query_role(self):
        assert map_roles(frozenset({"rag-user"}), _settings()) == {Role.QUERIER}

    def test_admin_implies_querier_and_auditor_by_default(self):
        assert map_roles(frozenset({"rag-admin"}), _settings()) == {Role.ADMIN, Role.QUERIER, Role.AUDITOR}

    def test_admin_without_audit_access(self):
        roles = map_roles(frozenset({"rag-admin"}), _settings(admin_can_read_audit=False))
        assert roles == {Role.ADMIN, Role.QUERIER}

    def test_auditor_only(self):
        assert map_roles(frozenset({"rag-auditor"}), _settings()) == {Role.AUDITOR}

    def test_unmapped_values_grant_nothing(self):
        assert map_roles(frozenset({"finance", "hr"}), _settings()) == frozenset()

    def test_multiple_configured_values(self):
        settings = _settings(query_roles="analyst, viewer")
        assert map_roles(frozenset({"viewer"}), settings) == {Role.QUERIER}


class TestAuthSettings:
    def test_defaults_are_disabled(self):
        assert AuthSettings().mode == "disabled"

    def test_csv_properties(self):
        settings = AuthSettings(algorithms="RS256, ES256", trusted_proxy_cidrs="10.0.0.0/8,,127.0.0.1")
        assert settings.algorithm_list == ["RS256", "ES256"]
        assert settings.trusted_proxy_list == ["10.0.0.0/8", "127.0.0.1"]
