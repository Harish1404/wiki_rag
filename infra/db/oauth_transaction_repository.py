"""
MongoDB adapter for the OAuthTransactionRepository port ("oauth_transactions").

A transaction is created when the user clicks Connect and consumed by the OAuth
callback. consume() is one atomic find_one_and_delete, so the same state can
never be used twice, even if two callbacks arrive at the same moment. A TTL
index deletes abandoned (expired) transactions automatically.
"""

from datetime import datetime, timezone
from typing import Any

from bson import Binary

from app.connectors.models import OAuthTransaction
from infra.db.mongo import get_oauth_transactions_collection


def _to_transaction(document: dict[str, Any]) -> OAuthTransaction:
    return OAuthTransaction(
        id=str(document["_id"]),
        state_hash=document["state_hash"],
        user_id=document["user_id"],
        provider_key=document["provider_key"],
        return_origin=document["return_origin"],
        pkce_verifier_ciphertext=bytes(document["pkce_verifier_ciphertext"]),
        expires_at=document["expires_at"],
        consumed_at=document.get("consumed_at"),
        created_at=document["created_at"],
    )


class MongoOAuthTransactionRepository:

    async def ensure_indexes(self) -> None:
        collection = get_oauth_transactions_collection()
        await collection.create_index("state_hash", unique=True)
        # MongoDB removes each document once its expires_at has passed.
        await collection.create_index("expires_at", expireAfterSeconds=0)

    async def create(
        self,
        state_hash: str,
        user_id: str,
        provider_key: str,
        return_origin: str,
        pkce_verifier_ciphertext: bytes,
        expires_at: datetime,
    ) -> OAuthTransaction:
        document = {
            "state_hash": state_hash,
            "user_id": user_id,
            "provider_key": provider_key,
            "return_origin": return_origin,
            "pkce_verifier_ciphertext": Binary(pkce_verifier_ciphertext),
            "expires_at": expires_at,
            "consumed_at": None,
            "created_at": datetime.now(timezone.utc),
        }
        result = await get_oauth_transactions_collection().insert_one(document)
        document["_id"] = result.inserted_id
        return _to_transaction(document)

    async def consume(self, state_hash: str, provider_key: str, now: datetime) -> OAuthTransaction | None:
        # Read-and-delete in one atomic step: single use, and the encrypted PKCE
        # verifier leaves the database the moment it's needed.
        document = await get_oauth_transactions_collection().find_one_and_delete(
            {
                "state_hash": state_hash,
                "provider_key": provider_key,
                "consumed_at": None,
                "expires_at": {"$gt": now},
            }
        )
        if not document:
            return None
        document["consumed_at"] = now
        return _to_transaction(document)
