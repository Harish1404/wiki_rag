from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.auth.models import Role
from app.routes.dependencies import CurrentUser

router = APIRouter(tags=["Users"])


class UserResponse(BaseModel):
    """The signed-in user's application profile. Never includes tokens or secrets."""

    id: str
    email: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    role: Role
    created_at: datetime
    last_login: Optional[datetime]


@router.get("/me", response_model=UserResponse)
async def read_me(current_user: CurrentUser) -> UserResponse:
    """Returns the profile of the user who owns the session token."""
    return UserResponse(**current_user.model_dump())
