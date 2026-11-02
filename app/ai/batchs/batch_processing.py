import asyncio
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable

logger = logging.getLogger("uvicorn.error")


# ─────────────────────────────────────────────────────────
# Settings: pass one of these to BatchProcess, or your own values
#
#   batch_size          - items sent in one request
#   max_concurrency     - batches allowed to run at the same time
#   requests_per_minute - request limit (None = no limit)
#   tokens_per_minute   - estimated token limit (None = no limit)
#   max_retries         - total attempts per batch before giving up
# ─────────────────────────────────────────────────────────

# Local model: no API limits, and it's CPU-bound, so run one batch at a time
LOCAL_EMBEDDING_SETTINGS = {
    "batch_size": 32,
    "max_concurrency": 1,
    "requests_per_minute": None,
    "tokens_per_minute": None,
    "max_retries": 1,
}

# gemini-embedding-001: kept below the reported free tier limits of
# 100 requests/min and 30k tokens/min. Check the real limits in AI Studio.
GEMINI_FREE_TIER_SETTINGS = {
    "batch_size": 20,
    "max_concurrency": 2,
    "requests_per_minute": 80,
    "tokens_per_minute": 25_000,
    "max_retries": 5,
}

BASE_RETRY_DELAY = 5.0    # seconds, first retry waits up to this long
MAX_RETRY_DELAY = 60.0    # seconds, longest possible wait between retries
RATE_LIMIT_WINDOW = 60.0  # seconds, the "per minute" window for the limits
CHARS_PER_TOKEN = 4       # rough estimate: 1 token is about 4 characters


class BatchProcess:

    def __init__(
        self,
        batch_size: int,
        max_concurrency: int,
        requests_per_minute: int | None = None,
        tokens_per_minute: int | None = None,
        max_retries: int = 3,
        window_seconds: float = RATE_LIMIT_WINDOW
    ):
        if batch_size < 1 or max_concurrency < 1:
            raise ValueError("batch_size and max_concurrency must be at least 1")

        self.batch_size = batch_size
        self.max_concurrency = max_concurrency
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self.max_retries = max_retries
        self.window_seconds = window_seconds

        # Requests sent in the current window, as (timestamp, tokens)
        self._window = deque()
        self._lock = asyncio.Lock()

    # ─────────────────────────────────────────────────────────
    # Step 1: Split the items into batches
    # ─────────────────────────────────────────────────────────

    def create_batches(self, items: list) -> list[list]:
        return [items[i:i + self.batch_size] for i in range(0, len(items), self.batch_size)]

    # ─────────────────────────────────────────────────────────
    # Step 2: Stay under the per-minute limits
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(batch: list) -> int:
        return sum(len(str(item)) for item in batch) // CHARS_PER_TOKEN

    async def _wait_for_rate_limit(self, tokens: int) -> None:
        """Waits until sending this request would stay within both limits, then records it."""

        if self.requests_per_minute is None and self.tokens_per_minute is None:
            return

        if self.tokens_per_minute is not None and tokens > self.tokens_per_minute:
            logger.warning(
                f"Batch of ~{tokens} tokens is over the {self.tokens_per_minute} tokens/min limit, "
                "lower batch_size to avoid rate limit errors"
            )

        # Only one batch checks at a time, so two batches can't claim the same quota
        async with self._lock:
            while True:
                now = time.monotonic()

                # Forget requests that are older than the window
                while self._window and now - self._window[0][0] >= self.window_seconds:
                    self._window.popleft()

                used_tokens = sum(entry[1] for entry in self._window)

                requests_ok = (
                    self.requests_per_minute is None
                    or len(self._window) < self.requests_per_minute
                )

                # An empty window always passes, so an oversized batch can't wait forever
                tokens_ok = (
                    self.tokens_per_minute is None
                    or not self._window
                    or used_tokens + tokens <= self.tokens_per_minute
                )

                if requests_ok and tokens_ok:
                    self._window.append((now, tokens))
                    return

                # Sleep until the oldest request leaves the window, then check again
                wait_time = self._window[0][0] + self.window_seconds - now
                logger.info(f"Rate limit reached, waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)

    # ─────────────────────────────────────────────────────────
    # Step 3: Process one batch with retries
    # ─────────────────────────────────────────────────────────

    async def _process_batch(
        self,
        func: Callable[[list], Awaitable[list]],
        batch: list,
        index: int,
        total: int,
        semaphore: asyncio.Semaphore,
        stop: asyncio.Event
    ) -> list:

        # The semaphore caps how many batches run at the same time
        async with semaphore:
            # Another batch failed while this one waited, so don't spend quota on it
            if stop.is_set():
                return []

            tokens = self._estimate_tokens(batch)

            for attempt in range(1, self.max_retries + 1):
                # Inside the loop because retries use up quota too
                await self._wait_for_rate_limit(tokens)

                try:
                    results = await func(batch)

                    # A short result would put the wrong embedding on the wrong chunk
                    if len(results) != len(batch):
                        raise ValueError(f"Expected {len(batch)} results, got {len(results)}")

                    logger.info(f"Batch {index}/{total} done ({len(batch)} items)")
                    return results

                except Exception as error:
                    if attempt >= self.max_retries:
                        logger.error(f"Batch {index}/{total} failed after {attempt} attempts: {error}")
                        stop.set()
                        raise

                    # Exponential backoff with full jitter
                    backoff = min(MAX_RETRY_DELAY, BASE_RETRY_DELAY * (2 ** (attempt - 1)))
                    sleep_time = random.uniform(0, backoff)

                    logger.warning(
                        f"Batch {index}/{total} attempt {attempt}/{self.max_retries} failed: {error}. "
                        f"Retrying in {sleep_time:.1f}s"
                    )
                    await asyncio.sleep(sleep_time)

    # ─────────────────────────────────────────────────────────
    # Step 4: Put it together — items -> results, in the same order
    # ─────────────────────────────────────────────────────────

    async def run(self, items: list, func: Callable[[list], Awaitable[list]]) -> list:
        """
        Splits items into batches and runs `func` on each one.

        `func` takes a list of items and returns a list with one result per item.
        Results come back in the same order as `items`. If a batch fails on every
        attempt, the remaining batches are cancelled and the error is raised.
        """
        if not items:
            return []

        batches = self.create_batches(items)
        total = len(batches)
        semaphore = asyncio.Semaphore(self.max_concurrency)
        stop = asyncio.Event()
        started_at = time.monotonic()

        logger.info(
            f"Batch processing started: {len(items)} items in {total} batches "
            f"(batch_size={self.batch_size}, max_concurrency={self.max_concurrency})"
        )

        tasks = [
            asyncio.create_task(self._process_batch(func, batch, index, total, semaphore, stop))
            for index, batch in enumerate(batches, start = 1)
        ]

        try:
            # gather keeps the original order, even if batches finish out of order
            batch_results = await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions = True)
            raise

        results = [result for batch in batch_results for result in batch]

        logger.info(f"Batch processing finished: {len(results)} items in {time.monotonic() - started_at:.1f}s")
        return results
