import redis

from core.config import get_settings

redis_client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)

INDEX_LOCK_TTL_SECONDS = 600


def acquire_index_lock(repository_id: str) -> bool:
    """Try to grab the per-repo indexing lock; True if acquired, False if a clone/index job already holds it."""
    return bool(redis_client.set(f"lock:index:{repository_id}", "1", nx=True, ex=INDEX_LOCK_TTL_SECONDS))


def release_index_lock(repository_id: str) -> None:
    """Release the per-repo indexing lock so a future job (e.g. a retry) can acquire it."""
    redis_client.delete(f"lock:index:{repository_id}")


def is_index_locked(repository_id: str) -> bool:
    """Whether an indexing job currently holds this repo's lock — i.e. whether work is actually in flight.

    The only honest way to tell "still embedding" from "stopped part-way through embedding":
    the status column records the stage a repo reached, never whether anything is still
    running. The UI uses this to decide whether to offer a Resume button.
    """
    return bool(redis_client.exists(f"lock:index:{repository_id}"))
