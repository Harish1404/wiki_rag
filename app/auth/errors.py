"""
Authentication errors raised by the business logic.

The FastAPI layer (app/routes/dependencies.py) turns these into HTTP responses:
    AuthenticationError   -> 401 Unauthorized
    PermissionDeniedError -> 403 Forbidden
"""


class AuthenticationError(Exception):
    """The request has no valid session token (missing, invalid or expired)."""


class PermissionDeniedError(Exception):
    """The user is authenticated but not allowed to do this (e.g. deactivated)."""
