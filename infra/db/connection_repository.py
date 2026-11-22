"""
MongoDB adapter for the ConnectionRepository port ("mcp_connections").

Every query filters by user_id, so a user can only ever read or change their
own connections. Schema: infra/db/schemas.py. Model: app/connectors/models.py.
"""

from datetime import datetime, timezone
from typing import Any

from bson import Binary
from pymongo import ASCENDING, ReturnDocument

from app.connectors.models import (
    Connection,
    ConnectionStatus,
    ConnectorDefinition,
    RemoteAccount,
)
from infra.db.mongo import get_mcp_connections_collection


def _to_connection(document: dict[str, Any]) -> Connection:
    return Connection(
        id=str(document["_id"]),
        user_id=document["user_id"],
        provider_key=document["provider_key"],
        server_url=document["server_url"],
        transport=document["transport"],
        auth_type=document["auth_type"],
        status=ConnectionStatus(document["status"]),
        scopes=document.get("scopes", []),
        remote_account_id=document.get("remote_account_id"),
        remote_account_login=document.get("remote_account_login"),
        token_ciphertext=bytes(document["token_ciphertext"]),
        expires_at=document.get("expires_at"),
        last_error_code=document.get("last_error_code"),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
        last_connected_at=document.get("last_connected_at"),
    )


class MongoConnectionRepository:

    async def ensure_indexes(self) -> None:
        # One connection per user per provider (no duplicate GitHub links).
        await get_mcp_connections_collection().create_index(
            [("user_id", ASCENDING), ("provider_key", ASCENDING)], unique=True
        )

    async def list_for_user(self, user_id: str) -> list[Connection]:
        cursor = get_mcp_connections_collection().find({"user_id": user_id})
        return [_to_connection(document) async for document in cursor]

    async def get(self, user_id: str, provider_key: str) -> Connection | None:
        document = await get_mcp_connections_collection().find_one(
            {"user_id": user_id, "provider_key": provider_key}
        )
        return _to_connection(document) if document else None

    async def upsert_connected(
        self,
        user_id: str,
        definition: ConnectorDefinition,
        account: RemoteAccount,
        token_ciphertext: bytes,
        scopes: list[str],
        expires_at: datetime | None,
    ) -> Connection:
        now = datetime.now(timezone.utc)
        document = await get_mcp_connections_collection().find_one_and_update(
            {"user_id": user_id, "provider_key": definition.key},
            {
                "$set": {
                    "server_url": definition.server_url,
                    "transport": definition.transport,
                    "auth_type": definition.auth_type,
                    "status": ConnectionStatus.CONNECTED.value,
                    "scopes": scopes,
                    "remote_account_id": account.id,
                    "remote_account_login": account.login,
                    "token_ciphertext": Binary(token_ciphertext),
                    "expires_at": expires_at,
                    "last_error_code": None,
                    "updated_at": now,
                    "last_connected_at": now,
                },
                "$setOnInsert": {"user_id": user_id, "provider_key": definition.key, "created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return _to_connection(document)

    async def update_token(
        self,
        user_id: str,
        provider_key: str,
        previous_ciphertext: bytes,
        token_ciphertext: bytes,
        expires_at: datetime | None,
    ) -> bool:
        result = await get_mcp_connections_collection().update_one(
            {
                "user_id": user_id,
                "provider_key": provider_key,
                "token_ciphertext": Binary(previous_ciphertext),
            },
            {
                "$set": {
                    "token_ciphertext": Binary(token_ciphertext),
                    "expires_at": expires_at,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        return result.modified_count == 1

    async def set_status(
        self, user_id: str, provider_key: str, status: ConnectionStatus, error_code: str | None
    ) -> None:
        await get_mcp_connections_collection().update_one(
            {"user_id": user_id, "provider_key": provider_key},
            {"$set": {"status": status.value, "last_error_code": error_code, "updated_at": datetime.now(timezone.utc)}},
        )

    async def delete(self, user_id: str, provider_key: str) -> bool:
        result = await get_mcp_connections_collection().delete_one(
            {"user_id": user_id, "provider_key": provider_key}
        )
        return result.deleted_count == 1
