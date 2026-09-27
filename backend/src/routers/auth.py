from fastapi import APIRouter
from src.dependencies import PrincipalDep
from src.schemas.api.auth import PrincipalResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/me", response_model=PrincipalResponse)
def who_am_i(principal: PrincipalDep) -> PrincipalResponse:
    """Return the resolved caller: subject, mapped roles, groups and auth method."""
    return PrincipalResponse(
        subject=principal.subject,
        roles=sorted(role.value for role in principal.roles),
        groups=sorted(principal.groups),
        raw_roles=sorted(principal.raw_roles),
        auth_method=principal.auth_method,
        api_key_id=principal.api_key_id,
        application_id=principal.application_id,
    )
