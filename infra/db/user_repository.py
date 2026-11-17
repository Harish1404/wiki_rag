"""
MongoDB adapter for the UserRepository port (app/auth/ports.py).

Document shape in the "users" collection:
    {
        "_id": ObjectId,
        "clerk_user_id": "user_xxx",   # unique index, the link to Clerk
        "email": str | None,
        "first_name": str | None,
        "last_name": str | None,
        "role": "user" | "admin",
        "is_active": bool,
        "created_at": datetime (UTC),
        "last_login": datetime (UTC) | None,
    }

No passwords, tokens or secrets are ever stored here: Clerk owns credentials.
"""

from datetime import datetime, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.auth.models import IdentityProfile, User
from infra.db.mongo import get_users_collection


def _to_user(document: dict[str, Any]) -> User:
    """Converts a MongoDB document into the domain User (ObjectId -> str id)."""
    return User(
        id=str(document["_id"]),
        clerk_user_id=document["clerk_user_id"],
        email=document.get("email"),
        first_name=document.get("first_name"),
        last_name=document.get("last_name"),
        role=document.get("role", "user"),
        is_active=document.get("is_active", True),
        created_at=document["created_at"],
        last_login=document.get("last_login"),
    )


class MongoUserRepository:

    async def ensure_indexes(self) -> None:
        # The unique index is what makes duplicate users impossible, even when
        # two first requests from the same user arrive at the same moment.
        await get_users_collection().create_index("clerk_user_id", unique=True)

    async def get_by_clerk_id(self, clerk_user_id: str) -> User | None:
        document = await get_users_collection().find_one({"clerk_user_id": clerk_user_id})
        return _to_user(document) if document else None

    async def create_if_absent(self, clerk_user_id: str, profile: IdentityProfile) -> User:
        now = datetime.now(timezone.utc)

        try:
            # Upsert + $setOnInsert: inserts only if no document matches,
            # otherwise returns the existing one untouched. This is a single
            # atomic operation in MongoDB.
            document = await get_users_collection().find_one_and_update(
                {"clerk_user_id": clerk_user_id},
                {
                    "$setOnInsert": {
                        "clerk_user_id": clerk_user_id,
                        "email": profile.email,
                        "first_name": profile.first_name,
                        "last_name": profile.last_name,
                        "role": "user",
                        "is_active": True,
                        "created_at": now,
                        "last_login": now,
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # Two concurrent upserts can race; the loser hits the unique index.
            # The winner already created the user, so just read it.
            document = await get_users_collection().find_one({"clerk_user_id": clerk_user_id})

        return _to_user(document)

    async def touch_last_login(self, clerk_user_id: str) -> None:
        await get_users_collection().update_one(
            {"clerk_user_id": clerk_user_id},
            {"$set": {"last_login": datetime.now(timezone.utc)}},
        )
