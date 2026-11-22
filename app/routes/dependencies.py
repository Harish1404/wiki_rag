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

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.errors import AuthenticationError, PermissionDeniedError
from app.auth.models import User
from app.core.container import auth_service


logger = logging.getLogger(__name__)


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

    except Exception as error:
        # Something behind the auth check broke (MongoDB down, Clerk API unreachable while
        # creating a new user...). Without this, FastAPI answers with a bare 500 that has
        # no CORS headers, and the browser reports it as "cannot reach the server".
        # The traceback goes to the server log only; request headers (the token) are never logged.
        logger.exception("Auth check failed unexpectedly")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign-in service is temporarily unavailable. Please try again.",
        ) from error


# Shorthand so routes can write `current_user: CurrentUser`.
CurrentUser = Annotated[User, Depends(get_current_user)]
