"""ConnectorService: OAuth state handling, ownership, health check, secrets never leaking."""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from app.connectors.errors import ConnectorError, ConnectorErrorCode, MCPAuthError
from app.connectors.models import ConnectionStatus, TokenBundle
from app.connectors.service import hash_state, pkce_pair
from tests.fakes import ORIGIN, FakeMCP, FakeOAuth, make_service, make_user

ALICE = make_user("alice")
BOB = make_user("bob")


async def connect(service, user=ALICE):
    url = await service.start_connect(user, "github", ORIGIN)
    state = parse_qs(urlsplit(url).query)["state"][0]
    return await service.complete_callback("github", "the-code", state, None), state


# ── Starting a connection ────────────────────────────────────────────

async def test_start_rejects_unlisted_origin():
    with pytest.raises(ConnectorError) as raised:
        await make_service().start_connect(ALICE, "github", "https://evil.example")
    assert raised.value.code == ConnectorErrorCode.INVALID_RETURN_ORIGIN


async def test_start_rejects_unknown_connector():
    with pytest.raises(ConnectorError) as raised:
        await make_service().start_connect(ALICE, "nope", ORIGIN)
    assert raised.value.code == ConnectorErrorCode.UNKNOWN_CONNECTOR


async def test_connectors_disabled_without_encryption_key():
    service = make_service(cipher=False)
    with pytest.raises(ConnectorError) as raised:
        await service.start_connect(ALICE, "github", ORIGIN)
    assert raised.value.code == ConnectorErrorCode.CONNECTOR_UNAVAILABLE
    assert (await service.list_for_user(ALICE))[0].available is False


async def test_state_is_stored_hashed_and_pkce_verifier_encrypted():
    service = make_service()
    url = await service.start_connect(ALICE, "github", ORIGIN)
    state = parse_qs(urlsplit(url).query)["state"][0]
    [(stored_hash, transaction)] = service.transactions.docs.items()
    assert stored_hash == hash_state(state) != state
    assert transaction.user_id == ALICE.id
    assert b"code" not in transaction.pkce_verifier_ciphertext  # opaque ciphertext


def test_pkce_challenge_is_s256_of_verifier():
    import base64
    import hashlib

    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


# ── Callback ─────────────────────────────────────────────────────────

async def test_successful_callback_connects_and_sends_pkce_verifier():
    oauth = FakeOAuth()
    service = make_service(oauth=oauth)
    result, _ = await connect(service)

    assert result.result == "connected"
    assert result.return_origin == ORIGIN
    connection = await service.connections.get(ALICE.id, "github")
    assert connection.status == ConnectionStatus.CONNECTED
    assert connection.remote_account_login == "octocat"
    assert oauth.last_verifier  # the verifier survived the round trip
    assert b"gho_SECRET_ACCESS_TOKEN" not in connection.token_ciphertext


async def test_state_replay_is_rejected():
    service = make_service()
    first, state = await connect(service)
    assert first.result == "connected"
    replay = await service.complete_callback("github", "the-code", state, None)
    assert replay.result == "error"
    assert replay.code == ConnectorErrorCode.OAUTH_STATE_INVALID.value


async def test_unknown_or_missing_state_is_rejected():
    service = make_service()
    for state in (None, "made-up-state"):
        result = await service.complete_callback("github", "the-code", state, None)
        assert result.result == "error"
        assert result.code == ConnectorErrorCode.OAUTH_STATE_INVALID.value
    assert service.connections.docs == {}


async def test_expired_state_is_rejected():
    service = make_service()
    url = await service.start_connect(ALICE, "github", ORIGIN)
    state = parse_qs(urlsplit(url).query)["state"][0]
    transaction = service.transactions.docs[hash_state(state)]
    service.transactions.docs[hash_state(state)] = transaction.model_copy(
        update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
    )
    result = await service.complete_callback("github", "the-code", state, None)
    assert result.code == ConnectorErrorCode.OAUTH_STATE_INVALID.value


async def test_state_for_another_connector_is_rejected():
    service = make_service()
    url = await service.start_connect(ALICE, "github", ORIGIN)
    state = parse_qs(urlsplit(url).query)["state"][0]
    result = await service.complete_callback("slack", "the-code", state, None)
    assert result.code == ConnectorErrorCode.OAUTH_STATE_INVALID.value


async def test_denied_consent_and_missing_code():
    service = make_service()
    url = await service.start_connect(ALICE, "github", ORIGIN)
    state = parse_qs(urlsplit(url).query)["state"][0]
    denied = await service.complete_callback("github", None, state, "access_denied")
    assert (denied.result, denied.code) == ("denied", ConnectorErrorCode.OAUTH_DENIED.value)

    url = await service.start_connect(ALICE, "github", ORIGIN)
    state = parse_qs(urlsplit(url).query)["state"][0]
    missing = await service.complete_callback("github", None, state, None)
    assert missing.result == "error"
    assert service.connections.docs == {}


async def test_not_connected_when_mcp_health_check_fails():
    service = make_service(mcp=FakeMCP(fail=MCPAuthError()))
    result, _ = await connect(service)
    assert result.result == "error"
    assert await service.connections.get(ALICE.id, "github") is None


# ── Ownership (IDOR) ─────────────────────────────────────────────────

async def test_users_only_see_and_use_their_own_connection():
    mcp = FakeMCP()
    service = make_service(mcp=mcp)
    await connect(service, ALICE)

    assert (await service.list_for_user(BOB))[0].status == "not_connected"
    assert await service.get_tools(BOB, "github") is None
    assert await service.disconnect(BOB, "github") is False
    assert await service.connections.get(ALICE.id, "github") is not None  # Bob couldn't touch it

    session = await service.get_tools(ALICE, "github")
    assert session.tools and session.account_login == "octocat"


async def test_disconnect_revokes_and_deletes():
    oauth = FakeOAuth()
    service = make_service(oauth=oauth)
    await connect(service)
    assert await service.disconnect(ALICE, "github") is True
    assert oauth.revoked == ["gho_SECRET_ACCESS_TOKEN"]
    assert await service.get_tools(ALICE, "github") is None


# ── Tokens at use time ───────────────────────────────────────────────

async def test_rejected_token_marks_needs_reauth():
    mcp = FakeMCP()
    service = make_service(mcp=mcp)
    await connect(service)
    mcp.fail = MCPAuthError()
    assert await service.get_tools(ALICE, "github") is None
    assert (await service.connections.get(ALICE.id, "github")).status == ConnectionStatus.NEEDS_REAUTH


async def test_expiring_token_is_refreshed_and_rotation_saved():
    expiring = TokenBundle(
        access_token="gho_OLD", refresh_token="rt_OLD",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    oauth = FakeOAuth(tokens=expiring)
    mcp = FakeMCP()
    service = make_service(oauth=oauth, mcp=mcp)
    await connect(service)

    assert await service.get_tools(ALICE, "github")
    assert mcp.tokens_seen[-1] == "gho_REFRESHED"
    stored = service._decrypt_tokens((await service.connections.get(ALICE.id, "github")).token_ciphertext)
    assert stored.refresh_token == "rt_NEW"


async def test_concurrent_refresh_calls_provider_once():
    import asyncio

    expiring = TokenBundle(
        access_token="gho_OLD", refresh_token="rt_OLD",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    oauth = FakeOAuth(tokens=expiring)
    service = make_service(oauth=oauth)
    await connect(service)

    await asyncio.gather(*(service.get_tools(ALICE, "github") for _ in range(5)))
    assert oauth.refresh_calls == 1


# ── No secrets in views or logs ──────────────────────────────────────

async def test_views_and_logs_never_contain_tokens(caplog):
    caplog.set_level(logging.DEBUG)
    service = make_service()
    await connect(service)
    await service.get_tools(ALICE, "github")
    await service.disconnect(ALICE, "github")

    views = (await service.list_for_user(ALICE))
    dumped = " ".join(view.model_dump_json() for view in views)
    for secret in ("gho_SECRET_ACCESS_TOKEN", "the-code"):
        assert secret not in dumped
        assert secret not in caplog.text
