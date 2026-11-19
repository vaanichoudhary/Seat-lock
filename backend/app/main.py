import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends
from app.core.config import settings
from app.core.db import get_db
from app.core.redis_client import redis_client
from sqlalchemy import text
from app.api import auth, bookings, events, holds, payments
from app.api.deps import require_role
from app.models import User, UserRole
from app.workers.cleanup import run_cleanup_once
from app.workers.outbox import process_outbox_once
from app.workers.runner import run_periodically

# show our own INFO logs (uvicorn only sets up logging for its own messages)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # everything before `yield` runs once when the server starts
    tasks = []
    if settings.run_workers:
        tasks = [
            asyncio.create_task(run_periodically("cleanup", run_cleanup_once, settings.cleanup_interval_seconds)),
            asyncio.create_task(run_periodically("outbox", process_outbox_once, settings.outbox_interval_seconds)),
        ]
    yield
    # everything after `yield` runs once when the server stops
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)  # wait until they have really stopped


app= FastAPI(title="Seat Lock", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(events.router)
app.include_router(holds.router)
app.include_router(bookings.router)
app.include_router(payments.router)

@app.get('/health/db')
async def health(db: AsyncSession=Depends(get_db)):
    result = await db.execute(text("SELECT 1"))
    return {"db": result.scalar()}

@app.get("/health/redis")
async def health_redis():
    result = await redis_client.ping()
    return {"redis": result}

@app.get("/admin-only")
async def admin_only(user: User = Depends(require_role(UserRole.ADMIN))):
    return {"hello": user.username}