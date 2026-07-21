from celery import Celery

from core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "noetra",
    broker=settings.celery_broker_url,
    backend=settings.redis_url,
)
celery_app.autodiscover_tasks(["worker"])
