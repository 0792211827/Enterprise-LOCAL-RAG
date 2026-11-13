from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class PrincipalResponse(BaseModel):
    """The caller as the platform sees them, for debugging claim mapping."""

    subject: str
    roles: List[str] = Field(description="Platform roles after claim mapping")
    groups: List[str] = Field(default_factory=list)
    raw_roles: List[str] = Field(default_factory=list, description="IdP role values before mapping")
    auth_method: str
    api_key_id: Optional[UUID] = None
    application_id: Optional[UUID] = None
