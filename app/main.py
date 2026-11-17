from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import chat, users
from app.core.config import settings
from app.core.container import user_repository
from infra.db.mongo import connect_to_mongo, close_mongo
from app.ai.rag.pipeline import rag_pipeline

from contextlib import asynccontextmanager

from pathlib import Path
import logging


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("uvicorn.error")

UPLOADS_DIR = Path(__file__).resolve().parents[1] / "uploads"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:

        logger.info("App startup: connecting to MongoDB...")
        await connect_to_mongo()

        # Unique index on users.clerk_user_id prevents duplicate profiles.
        await user_repository.ensure_indexes()

        # Ensure Atlas Vector Search index exists before ingesting
        from app.ai.rag.vector_store import vector_store
        await vector_store.ensure_vector_index()

        logger.info("App startup: data ingestion is running...")
        await rag_pipeline.data_ingestion(UPLOADS_DIR)

        yield

    finally:
        
        await close_mongo()
        logger.info("App shutdown")



app = FastAPI(lifespan = lifespan)


@app.get("/")
def landing_page():
    return {"message":"Hi this is your backend"}

@app.get("/health")
def health():
    return {"status": "ok"}

# Public:    /  and  /health
# Protected: /llm  and  /me   (they use Depends(get_current_user))
app.include_router(chat.router)
app.include_router(users.router)

# Only our frontend may call the API from a browser. Auth uses the
# Authorization header (not cookies), so credentials are not needed.
allowed_origins = [origin for origin in [settings.FRONTEND_URL, "http://localhost:3000"] if origin]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

