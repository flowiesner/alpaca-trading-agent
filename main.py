import logging
import sys
from logging.handlers import RotatingFileHandler

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from storage.db import init_db
from reasoning.decision import run_decision
from reasoning.daily_review import run_daily_review

_fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s – %(message)s")

_file_handler = RotatingFileHandler(
    "trading.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)

_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _stream_handler])
logger = logging.getLogger(__name__)

TIMEZONE = "Europe/Vienna"


def main():
    init_db()
    logger.info("Database initialized")

    scheduler = BlockingScheduler(timezone=TIMEZONE, job_defaults={"max_instances": 1})

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
