from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.services.refresh import RefreshConflict, RefreshService
from app.services.gmail import GmailError, GmailService

MANILA = ZoneInfo("Asia/Manila")


async def scheduled_refresh(service: RefreshService):
    try:
        return await service.start("scheduled")
    except RefreshConflict:
        # The active search already covers this occurrence; never overlap it.
        return None


async def scheduled_gmail_check(service: GmailService):
    if not service.connected:
        return None
    try:
        return await service.check()
    except GmailError:
        return None


def build_scheduler(service: RefreshService, gmail: GmailService | None = None) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=MANILA)
    scheduler.add_job(
        scheduled_refresh,
        CronTrigger(hour=8, minute=0, timezone=MANILA),
        args=[service], id="daily_job_refresh", name="Daily job search (Manila 08:00)",
        coalesce=True, max_instances=1, misfire_grace_time=3600,
    )
    if gmail is not None:
        scheduler.add_job(
            scheduled_gmail_check, CronTrigger(minute="*/15", timezone=MANILA), args=[gmail],
            id="gmail_readonly_check", name="Gmail read-only check (every 15 minutes)",
            coalesce=True, max_instances=1, misfire_grace_time=600,
        )
    return scheduler
