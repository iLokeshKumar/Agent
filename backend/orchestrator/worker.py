import logging
import time
import threading

from app.core.db import SessionLocal
from app.repository.task_repository import TaskRepository
from orchestrator.executor import execute_task


logger = logging.getLogger(__name__)

# How often the watchdog checks for stale tasks (seconds)
WATCHDOG_INTERVAL = 60


class TaskWorker:

    def __init__(self):
        self.running = False
        self.thread = None
        self.watchdog_thread = None

    def start(self):
        if self.running:
            return

        self.running = True

        self.thread = threading.Thread(
            target=self.run,
            daemon=True,
            name="task-worker",
        )
        self.thread.start()

        self.watchdog_thread = threading.Thread(
            target=self.watchdog,
            daemon=True,
            name="task-watchdog",
        )
        self.watchdog_thread.start()

        logger.info("Task worker and watchdog started")

    def stop(self):
        self.running = False

        if self.thread:
            self.thread.join(timeout=2)

        if self.watchdog_thread:
            self.watchdog_thread.join(timeout=2)

        logger.info("Task worker and watchdog stopped")

    def run(self):
        while self.running:
            db = SessionLocal()
            task = None

            try:
                repository = TaskRepository(db)

                task = repository.claim_next_task()

                if task is None:
                    time.sleep(0.5)
                    continue

                logger.info(
                    "Claimed task %s priority=%s type=%s",
                    task.id,
                    task.priority,
                    task.task_type,
                )

                from app.integrations.zoho_books import zoho_user_context
                with zoho_user_context(task.requested_by):
                    execute_task(db, task)

            except Exception as exc:
                logger.exception("Task execution failed")

                if task is not None:
                    repository = TaskRepository(db)

                    result = repository.fail_or_retry(
                        task,
                        str(exc),
                    )

                    logger.error(
                        "Task %s result=%s",
                        task.id,
                        result,
                    )

            finally:
                db.close()

    def watchdog(self):
        """Periodically recover tasks stuck in RUNNING state."""
        while self.running:
            time.sleep(WATCHDOG_INTERVAL)

            if not self.running:
                break

            db = SessionLocal()

            try:
                repository = TaskRepository(db)

                stale_tasks = repository.recover_stale_tasks()

                for task in stale_tasks:
                    logger.warning(
                        "Watchdog recovering stale task %s "
                        "(status=RUNNING, started_at=%s)",
                        task.id,
                        task.started_at,
                    )

                    result = repository.timeout_task(task)

                    logger.info(
                        "Watchdog: task %s -> %s",
                        task.id,
                        result,
                    )

            except Exception:
                logger.exception("Watchdog cycle failed")

            finally:
                db.close()


worker = TaskWorker()