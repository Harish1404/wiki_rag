"""
MongoDB schema for the connector collections.

The Pydantic models in app/connectors/models.py are the source of truth in
Python; these $jsonSchema validators make MongoDB itself refuse documents that
don't match (wrong types, missing fields, unknown status values). Together with
the indexes they are applied at startup by ensure_connector_schemas().

To change a field: update the Pydantic model, the repository, and the validator
here. On start, a missing collection is created with its validator; an existing
one is only changed (collMod) when its validator differs from the one here.
collMod needs the dbAdmin role on Atlas; a plain readWrite user gets a warning
and the old validator stays, but the app still starts.
"""

import logging

from pymongo.errors import CollectionInvalid, OperationFailure

from infra.db.mongo import MCP_CONNECTIONS, OAUTH_TRANSACTIONS, get_database


logger = logging.getLogger(__name__)

_DATE_OR_NULL = {"bsonType": ["date", "null"]}
_STRING_OR_NULL = {"bsonType": ["string", "null"]}

MCP_CONNECTIONS_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "user_id", "provider_key", "server_url", "transport", "auth_type",
            "status", "scopes", "token_ciphertext", "created_at", "updated_at",
        ],
        "properties": {
            "user_id": {"bsonType": "string", "description": "application users._id"},
            "provider_key": {"bsonType": "string"},
            "server_url": {"bsonType": "string"},
            "transport": {"enum": ["streamable_http"]},
            "auth_type": {"enum": ["oauth2"]},
            "status": {"enum": ["connected", "needs_reauth", "error"]},
            "scopes": {"bsonType": "array", "items": {"bsonType": "string"}},
            "remote_account_id": _STRING_OR_NULL,
            "remote_account_login": _STRING_OR_NULL,
            # Encrypted TokenBundle. Never plaintext; there is no field for a raw token.
            "token_ciphertext": {"bsonType": "binData"},
            "expires_at": _DATE_OR_NULL,
            "last_error_code": _STRING_OR_NULL,
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
            "last_connected_at": _DATE_OR_NULL,
        },
    }
}

OAUTH_TRANSACTIONS_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "state_hash", "user_id", "provider_key", "return_origin",
            "pkce_verifier_ciphertext", "expires_at", "created_at",
        ],
        "properties": {
            "state_hash": {"bsonType": "string", "minLength": 64, "maxLength": 64},
            "user_id": {"bsonType": "string"},
            "provider_key": {"bsonType": "string"},
            "return_origin": {"bsonType": "string"},
            "pkce_verifier_ciphertext": {"bsonType": "binData"},
            "expires_at": {"bsonType": "date"},
            "consumed_at": _DATE_OR_NULL,
            "created_at": {"bsonType": "date"},
        },
    }
}


async def _current_validator(name: str) -> dict | None:
    """The collection's validator, {} if it has none, or None if the collection doesn't exist."""
    cursor = await get_database().list_collections(filter={"name": name})
    async for info in cursor:
        return info.get("options", {}).get("validator", {})
    return None


async def _apply_validator(name: str, validator: dict) -> None:
    database = get_database()
    current = await _current_validator(name)

    if current is None:
        try:
            await database.create_collection(name, validator=validator, validationLevel="strict")
            logger.info("Created collection %s with schema validation", name)
        except CollectionInvalid:
            # Created by another process between our check and now: compare again next start.
            pass
        except OperationFailure as error:
            logger.warning(
                "Could not create %s with schema validation (%s); it will be created on first write "
                "without a validator. Pydantic models still validate every document.",
                name, error.code,
            )
        return

    if current == validator:
        return  # already up to date: normal starts need no admin rights

    try:
        await database.command({"collMod": name, "validator": validator, "validationLevel": "strict"})
        logger.info("Updated schema validation on %s", name)
    except OperationFailure as error:
        # Atlas "readWrite" users can't run collMod (it needs dbAdmin). Keep the existing
        # validator and carry on: a schema sync must never stop the app from starting.
        logger.warning(
            "Schema validator on %s is out of date but was not updated: the database user lacks "
            "the collMod (dbAdmin) privilege (code %s). Grant dbAdmin on this database, or update "
            "the validator in Atlas, to apply the new schema.",
            name, error.code,
        )


async def ensure_connector_schemas() -> None:
    await _apply_validator(MCP_CONNECTIONS, MCP_CONNECTIONS_SCHEMA)
    await _apply_validator(OAUTH_TRANSACTIONS, OAUTH_TRANSACTIONS_SCHEMA)
