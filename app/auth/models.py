"""
Authentication domain models.

These are plain pydantic models with no Clerk or MongoDB imports, so the
business logic in app/auth/service.py does not depend on any vendor.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


Role = Literal["user", "admin"]


class AuthPrincipal(BaseModel):
    """
    The identity proven by a verified session token.

    clerk_user_id comes ONLY from the verified token's "sub" claim,
    never from anything the client sends in the request body.
    """

    clerk_user_id: str
    session_id: Optional[str] = None


class IdentityProfile(BaseModel):
    """Basic profile details fetched from the identity provider (Clerk)."""

    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class User(BaseModel):
    """The application user stored in the MongoDB "users" collection."""

    id: str
    clerk_user_id: str
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: Role = "user"
    is_active: bool = True
    created_at: datetime
    last_login: Optional[datetime] = None
