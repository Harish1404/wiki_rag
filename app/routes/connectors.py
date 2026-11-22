"""
Connector API.

    GET    /connectors                   catalog + the signed-in user's connection status
    POST   /connectors/{key}/connect     start OAuth; returns the provider's authorization URL
    GET    /connectors/{key}/callback    OAuth redirect target (browser, no Clerk token: bound by state)
    DELETE /connectors/{key}             disconnect (revoke at the provider when possible)

Responses never contain tokens, codes, PKCE verifiers or raw OAuth state.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.connectors.errors import ConnectorError, ConnectorErrorCode
from app.connectors.models import ConnectorView
from app.core.container import connector_service
from app.routes.dependencies import CurrentUser


router = APIRouter(prefix="/connectors", tags=["Connectors"])

FRONTEND_CONNECTORS_PATH = "/connectors"

_STATUS_BY_CODE = {
    ConnectorErrorCode.UNKNOWN_CONNECTOR: status.HTTP_404_NOT_FOUND,
    ConnectorErrorCode.CONNECTOR_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    ConnectorErrorCode.INVALID_RETURN_ORIGIN: status.HTTP_403_FORBIDDEN,
}


def _http_error(error: ConnectorError) -> HTTPException:
    # detail stays a plain string (the frontend's API client reads it as the message);
    # the stable code goes in a header for anyone who needs to branch on it.
    return HTTPException(
        status_code=_STATUS_BY_CODE.get(error.code, status.HTTP_400_BAD_REQUEST),
        detail=error.message,
        headers={"X-Error-Code": error.code.value},
    )


class ConnectResponse(BaseModel):
    authorization_url: str


@router.get("", response_model=list[ConnectorView])
async def list_connectors(current_user: CurrentUser) -> list[ConnectorView]:
    return await connector_service.list_for_user(current_user)


@router.post("/{key}/connect", response_model=ConnectResponse)
async def start_connect(key: str, request: Request, current_user: CurrentUser) -> ConnectResponse:
    try:
        # The browser's Origin decides where the callback sends the user back to.
        # It's checked against an allowlist, so this can't become an open redirect.
        url = await connector_service.start_connect(current_user, key, request.headers.get("origin"))
    except ConnectorError as error:
        raise _http_error(error) from error
    return ConnectResponse(authorization_url=url)


@router.get("/{key}/callback", include_in_schema=False)
async def oauth_callback(
    key: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    result = await connector_service.complete_callback(key, code, state, error)

    origin = result.return_origin or connector_service.fallback_origin()
    if origin is None:
        raise HTTPException(status_code=400, detail="Connection could not be completed.")

    # Only non-secret values go into the URL.
    query = {"connector": result.provider_key, "result": result.result}
    if result.code:
        query["code"] = result.code
    # 303: the browser follows with a plain GET to the frontend page.
    return RedirectResponse(f"{origin}{FRONTEND_CONNECTORS_PATH}?{urlencode(query)}", status_code=303)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(key: str, current_user: CurrentUser) -> None:
    try:
        await connector_service.disconnect(current_user, key)
    except ConnectorError as error:
        raise _http_error(error) from error
