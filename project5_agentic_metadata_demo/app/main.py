from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from project5_agentic_metadata_demo.app.agent_service import router as agent_router
from project5_agentic_metadata_demo.app.database import init_db
from project5_agentic_metadata_demo.app.metadata_service import router as metadata_router
from project5_agentic_metadata_demo.app.seed import seed_database


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    seed_database()
    yield


app = FastAPI(title="Agentic Metadata Demo", lifespan=lifespan)
app.include_router(metadata_router)
app.include_router(agent_router)

