from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery("ceremoni", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="US/Central",
    enable_utc=True,
)

celery_app.conf.beat_schedule = {
    "sync-form-responses": {
        "task": "app.tasks.sync_form_responses",
        "schedule": 300.0,  # every 5 minutes
    },
}

celery_app.autodiscover_tasks(["app"])
