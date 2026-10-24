"""Document-processing worker: claims queued uploads from the `document_jobs`
table and ingests them. Run alongside the API with INLINE_WORKERS=false:

    uv run python -m policy_advisor.worker            # loop forever
    uv run python -m policy_advisor.worker --once     # drain the queue and exit

Several workers may run at once; SKIP LOCKED keeps them off each other's jobs,
and jobs abandoned by a dead worker are re-queued after a quarter of an hour.
"""

import argparse
import time

from policy_advisor.api.jobs import JobTable
from policy_advisor.api.services import AdvisorServices
from policy_advisor.config import get_settings
from policy_advisor.logging_utils import log_event


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process what is queued, then exit")
    args = parser.parse_args()

    settings = get_settings()
    services = AdvisorServices()
    jobs = JobTable()
    log_event(
        services.logger, "worker_started", worker_id=jobs.worker_id, inline=settings.inline_workers
    )
    while True:
        jobs.requeue_stale()
        ran = jobs.run_pending(services, limit=50)
        if args.once:
            log_event(services.logger, "worker_drained", ran=ran)
            return
        if ran == 0:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
