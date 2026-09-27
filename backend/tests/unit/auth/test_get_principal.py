"""The get_principal dependency: 401 shape and request.state propagation."""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from src.auth import AuthenticationError, Principal, Role
from src.dependencies import PrincipalDep


class _RejectingAuthenticator:
    mode = "jwt"

    async def authenticate(self, request):
        raise AuthenticationError("signature verification failed for kid k9")


class _FixedAuthenticator:
    mode = "jwt"

    async def authenticate(self, request):
        return Principal(subject="dana", roles=frozenset({Role.QUERIER}), auth_method="jwt")


def _app(authenticator) -> FastAPI:
    app = FastAPI()
    app.state.authenticator = authenticator

    @app.get("/who")
    def who(principal: PrincipalDep, request: Request):
        return {"subject": principal.subject, "state_subject": request.state.principal.subject}

    return app


def test_rejection_is_a_generic_401_with_challenge():
    response = TestClient(_app(_RejectingAuthenticator())).get("/who")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    # The reason stays in server logs; the client learns nothing about which check failed.
    assert response.json() == {"detail": "Not authenticated"}


def test_principal_is_injected_and_stored_on_request_state():
    response = TestClient(_app(_FixedAuthenticator())).get("/who")
    assert response.json() == {"subject": "dana", "state_subject": "dana"}


def test_app_without_lifespan_builds_authenticator_from_settings():
    app = FastAPI()

    @app.get("/who")
    def who(principal: PrincipalDep):
        return {"method": principal.auth_method}

    response = TestClient(app).get("/who")
    assert response.json() == {"method": "disabled"}  # default configuration
    assert app.state.authenticator.mode == "disabled"
