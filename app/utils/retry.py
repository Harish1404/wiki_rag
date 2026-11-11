import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def jitter_delay(attempt: int, base_delay: float, max_delay: float) -> float:
    """
    Exponential backoff with full jitter.

        backoff = min(max_delay, base_delay * 2 ** (attempt - 1))
        delay   = random.uniform(0, backoff)

    The randomness spreads retries out so many clients don't hit a
    rate-limited API again at the same moment.
    """
    backoff = min(max_delay, base_delay * (2 ** (attempt - 1)))
    return random.uniform(0, backoff)


async def retry_with_jitter(
    func: Callable[..., Awaitable[T]],
    *args,
    max_retries: int,
    base_delay: float,
    max_delay: float,
    label: str = "operation",
    **kwargs,
) -> T:
    """
    Awaits `func(*args, **kwargs)` up to `max_retries` times, sleeping
    `jitter_delay(...)` between attempts. Re-raises the last error.
    """
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")

    for attempt in range(1, max_retries + 1):
        try:
            return await func(*args, **kwargs)

        except Exception as error:
            if attempt >= max_retries:
                logger.error("%s failed after %d attempts: %s", label, attempt, error)
                raise

            delay = jitter_delay(attempt, base_delay, max_delay)
            logger.warning(
                "%s attempt %d/%d failed: %s. Retrying in %.1fs",
                label, attempt, max_retries, error, delay,
            )
            await asyncio.sleep(delay)

    raise AssertionError("unreachable")
