"""Background chat jobs (revision 3). The chat reply is produced in a worker
thread while the client polls and the speech bubble narrates the stage the
pipeline is in (`policy_advisor.progress`). In-process like the document
job table: a restart forgets in-flight jobs, and finished replies are already
in the conversation log by the time the client reads them.

A job is bound to the matter AND the user who submitted it; the router
checks both, so one user can never read another user's job (rule 1)."""

import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from policy_advisor.api.errors import ApiError
from policy_advisor.api.schemas import ChatJobError, ChatJobOut, ChatJobStatus, ChatReply, ChatStage
from policy_advisor.progress import stage_listener

# Finished jobs are kept this long so a slow poller still finds its reply.
RETAIN_SECONDS = 15 * 60
MAX_WORKERS = 4


@dataclass
class ChatJob:
    job_id: str
    matter_id: str
    username: str
    status: ChatJobStatus = "queued"
    stage: ChatStage | None = None
    stages: list[ChatStage] = field(default_factory=list)  # full history, for tests/logs
    reply: ChatReply | None = None
    error: ChatJobError | None = None
    created_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None

    def to_out(self) -> ChatJobOut:
        return ChatJobOut(
            job_id=self.job_id,
            status=self.status,
            stage=self.stage,
            stages=list(self.stages),
            reply=self.reply,
            error=self.error,
        )


class ChatJobTable:
    def __init__(self, max_workers: int = MAX_WORKERS) -> None:
        self._jobs: dict[str, ChatJob] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="chat-job")

    def submit(
        self, matter_id: str, username: str, work: Callable[[], ChatReply], logger=None
    ) -> ChatJob:
        job = ChatJob(job_id=uuid.uuid4().hex, matter_id=matter_id, username=username)
        with self._lock:
            self._prune()
            self._jobs[job.job_id] = job
        self._executor.submit(self._run, job, work, logger)
        return job

    def get(self, job_id: str, matter_id: str, username: str) -> ChatJob | None:
        """Only the submitting user, on the same matter, can see a job."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None or job.matter_id != matter_id or job.username != username:
            return None
        return job

    def _prune(self) -> None:
        now = time.monotonic()
        for job_id in [
            j.job_id
            for j in self._jobs.values()
            if j.finished_at is not None and now - j.finished_at > RETAIN_SECONDS
        ]:
            del self._jobs[job_id]

    def _run(self, job: ChatJob, work: Callable[[], ChatReply], logger) -> None:
        job.status = "running"

        def on_stage(stage: ChatStage) -> None:
            job.stage = stage
            job.stages.append(stage)

        try:
            with stage_listener(on_stage):
                job.reply = work()
            job.status = "done"
        except ApiError as exc:
            job.error = ChatJobError(code=exc.code, message=exc.message)
            job.status = "failed"
        except Exception as exc:  # never leak exception text to the client
            job.error = ChatJobError(
                code="internal_error", message="Something went wrong on the server."
            )
            job.status = "failed"
            if logger is not None:
                logger.error(
                    "chat_job_failed",
                    exc_info=exc,
                    extra={"fields": {"matter_id": job.matter_id, "job_id": job.job_id}},
                )
        finally:
            job.finished_at = time.monotonic()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
