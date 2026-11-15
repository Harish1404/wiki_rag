from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
import logging

logger = logging.getLogger("uvicorn")

class Database:
    client: AsyncIOMotorClient = None

db_instance = Database()


def get_database():
    """Returns the Motor database object. Requires connect_to_mongo() to have been called first."""
    if db_instance.client is None:
        raise RuntimeError("MongoDB client is not connected. Call connect_to_mongo() first.")
    return db_instance.client[settings.DB_NAME]


def get_chunks_collection():
    """Returns the 'chunks' collection from the connected database."""
    return get_database()["chunks"]


def get_sources_collection():
    """Returns the 'sources' collection from the connected database."""
    return get_database()["sources"]


def get_tables_collection():
    """Returns the 'tables' collection (structured tables extracted from PDFs)."""
    return get_database()["tables"]


async def connect_to_mongo():
    try:
        logger.info("⏳ Connecting to MongoDB...")
        db_instance.client = AsyncIOMotorClient(settings.MONGO_URL)

        # THE PING TEST (Crucial for Cloud)
        await db_instance.client.admin.command('ping')
        logger.info("✅ MongoDB Connected Successfully!")

    except Exception as e:
        logger.error(f"❌ MongoDB Connection Failed: {e}")
        raise e


async def close_mongo():
    if db_instance.client:
        db_instance.client.close()
        logger.info("🔒 MongoDB connection closed.")
