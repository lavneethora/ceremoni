import asyncio

from app.worker import celery_app
from app.db import async_session
from app.services import pipeline


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="app.tasks.process_recording")
def process_recording(recording_id: str):
    async def _process():
        async with async_session() as session:
            await pipeline.process(recording_id, session)
            await pipeline.generate_final_audio(recording_id, session)

    _run_async(_process())


@celery_app.task(name="app.tasks.sync_form_responses")
def sync_form_responses():
    from app.services.forms_sync import sync

    _run_async(sync())
