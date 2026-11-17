"""
MongoDB connection manager.

This file is the ONLY place that knows about Motor (async MongoDB driver).
If you ever switch to PostgreSQL, Redis, or another database,
you replace THIS file — nothing else in the app changes.

What this file does:
    1. Holds a single Motor client (db_instance)
    2. Provides getter functions for each collection
    3. Provides connect / close lifecycle functions
"""

from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Database singleton
# ──────────────────────────────────────────────────────────────
# We keep one global Motor client so every part of the app
# shares the same connection pool.

class Database:
    """Holds the Motor client. Only one instance exists (db_instance below)."""
    client: AsyncIOMotorClient = None


# This is the single instance — connect_to_mongo() fills it,
# close_mongo() shuts it down.
db_instance = Database()


# ──────────────────────────────────────────────────────────────
# Collection getters
# ──────────────────────────────────────────────────────────────
# Each getter returns a Motor collection object.
# Call these instead of hard-coding collection names everywhere.

def get_database():
    """
    Returns the Motor database object.
    Requires connect_to_mongo() to have been called first.
    """
    if db_instance.client is None:
        raise RuntimeError(
            "MongoDB client is not connected. Call connect_to_mongo() first."
        )
    return db_instance.client[settings.DB_NAME]


def get_chunks_collection():
    """Returns the 'chunks' collection (where document embeddings live)."""
    return get_database()["chunks"]


def get_sources_collection():
    """Returns the 'sources' collection (one record per ingested file)."""
    return get_database()["sources"]


def get_tables_collection():
    """Returns the 'tables' collection (structured tables from PDFs)."""
    return get_database()["tables"]


def get_users_collection():
    """Returns the 'users' collection (one application profile per Clerk user)."""
    return get_database()["users"]


# ──────────────────────────────────────────────────────────────
# Lifecycle — called from app/main.py at startup and shutdown
# ──────────────────────────────────────────────────────────────

async def connect_to_mongo():
    """
    Opens the Motor client and pings the server to confirm it's reachable.
    Called once at app startup (FastAPI lifespan).
    """
    try:
        logger.info("⏳ Connecting to MongoDB...")

        # Create the async MongoDB client
        db_instance.client = AsyncIOMotorClient(settings.MONGO_URL)

        # Ping confirms the connection actually works
        # (important for MongoDB Atlas — the client is lazy, so without
        #  a ping you won't know about auth or network errors until the
        #  first real query)
        await db_instance.client.admin.command("ping")

        logger.info("✅ MongoDB Connected Successfully!")

    except Exception as e:
        logger.error(f"❌ MongoDB Connection Failed: {e}")
        raise e


async def close_mongo():
    """Closes the Motor client. Called once at app shutdown."""
    if db_instance.client:
        db_instance.client.close()
        logger.info("🔒 MongoDB connection closed.")
