import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from storage.db import init_db
from reasoning.decision import run_decision
from reasoning.daily_review import run_daily_review

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s – %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

TIMEZONE = "Europe/Vienna"


def main():
    init_db()
    logger.info("Database initialized")

    scheduler = BlockingScheduler(timezone=TIMEZONE)

    for slot in config.DECISION_TIMES:
        scheduler.add_job(
            run_decision,
            CronTrigger(
                hour=slot["hour"],
                minute=slot["minute"],
                day_of_week="mon-fri",
                timezone=TIMEZONE,
            ),
            id=f"decision_{slot['hour']}_{slot['minute']}",
            name=f"Decision {slot['hour']:02d}:{slot['minute']:02d}",
            misfire_grace_time=300,
        )

    scheduler.add_job(
        run_daily_review,
        CronTrigger(
            hour=config.DAILY_REVIEW_TIME["hour"],
            minute=config.DAILY_REVIEW_TIME["minute"],
            day_of_week="mon-fri",
            timezone=TIMEZONE,
        ),
        id="daily_review",
        name="Daily Review",
        misfire_grace_time=300,
    )

    logger.info("Scheduler starting – jobs registered:")
    for job in scheduler.get_jobs():
        logger.info("  %s", job)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    main()
