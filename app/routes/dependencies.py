"""
FastAPI dependencies shared by routes.

Protect any endpoint by adding a parameter:

    @router.get("/something")
    async def something(current_user: CurrentUser):
        ...

FastAPI runs get_current_user before the endpoint. If the token is missing or
invalid the endpoint never runs and the client gets 401 (or 403 if the user is
deactivated).
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.errors import AuthenticationError, PermissionDeniedError
from app.auth.models import User
from app.core.container import auth_service


async def get_current_user(request: Request) -> User:
    try:
        return await auth_service.get_current_user(request.headers)

    except AuthenticationError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    except PermissionDeniedError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error


# Shorthand so routes can write `current_user: CurrentUser`.
CurrentUser = Annotated[User, Depends(get_current_user)]
