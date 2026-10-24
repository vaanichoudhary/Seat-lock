from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends
from app.core.db import get_db
from app.core.redis_client import redis_client
from sqlalchemy import text

app= FastAPI(title="Seat Lock")
@app.get('/health/db')
async def health(db: AsyncSession=Depends(get_db)):
    result = await db.execute(text("SELECT 1"))
    return {"db": result.scalar()}

@app.get("/health/redis")
async def health_redis():
    result = await redis_client.ping()
    return {"redis": result}
