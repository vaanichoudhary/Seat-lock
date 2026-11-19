import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal

logger = logging.getLogger("seatlock.workers")


async def run_periodically(
    name: str, job: Callable[[AsyncSession], Awaitable[object]], interval_seconds: float
) -> None:
    """Run `job` every `interval_seconds`, forever, until the task is cancelled."""
    logger.info("%s started (every %ss)", name, interval_seconds)
    while True:
        try:
            # a fresh session per run, so one bad run can't leave a broken session behind
            async with SessionLocal() as db:
                await job(db)
        except asyncio.CancelledError:
            raise  # the server is shutting down: stop the loop
        except Exception:
            # log and keep going; one failed run must not kill the job forever
            logger.exception("%s run failed", name)
        await asyncio.sleep(interval_seconds)
