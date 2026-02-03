"""Scheduled task management for Claude Code Telegram Bridge."""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class ScheduledTask:
    """A scheduled task definition."""
    task_id: str
    chat_id: int
    project_name: str
    prompt: str
    schedule_type: str  # "once", "daily", "weekly"
    next_run: str  # ISO datetime
    enabled: bool
    time_of_day: str  # "HH:MM" for daily/weekly
    day_of_week: Optional[int]  # 0-6 for weekly (0=Monday)
    last_run: Optional[str]  # ISO datetime

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduledTask":
        """Create from dictionary."""
        return cls(**data)


class ScheduledTaskManager:
    """Manages scheduled tasks with persistence."""

    def __init__(self, storage_path: str, check_interval: int = 30):
        """Initialize the scheduler.

        Args:
            storage_path: Directory to store scheduled tasks JSON
            check_interval: Seconds between scheduler checks (default 30)
        """
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.tasks_file = self.storage_path / "scheduled_tasks.json"
        self.check_interval = check_interval
        self._tasks: dict[str, ScheduledTask] = {}
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None
        self._callback: Optional[Callable[[ScheduledTask], Awaitable[None]]] = None
        self._load()

    def _load(self) -> None:
        """Load tasks from disk."""
        if not self.tasks_file.exists():
            return
        try:
            with open(self.tasks_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for task_data in data.get("tasks", []):
                    task = ScheduledTask.from_dict(task_data)
                    self._tasks[task.task_id] = task
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load scheduled tasks: {e}")

    def _save(self) -> None:
        """Save tasks to disk."""
        data = {
            "tasks": [task.to_dict() for task in self._tasks.values()]
        }
        with open(self.tasks_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def create_task(
        self,
        chat_id: int,
        project_name: str,
        prompt: str,
        schedule_type: str,
        run_time: datetime,
        time_of_day: str = "",
        day_of_week: Optional[int] = None,
    ) -> ScheduledTask:
        """Create a new scheduled task.

        Args:
            chat_id: Telegram chat ID to send results to
            project_name: Target project name
            prompt: The prompt to execute
            schedule_type: "once", "daily", or "weekly"
            run_time: When to first run (datetime)
            time_of_day: "HH:MM" format for recurring tasks
            day_of_week: 0-6 (Monday-Sunday) for weekly tasks

        Returns:
            The created ScheduledTask
        """
        task_id = uuid.uuid4().hex[:8]
        task = ScheduledTask(
            task_id=task_id,
            chat_id=chat_id,
            project_name=project_name,
            prompt=prompt,
            schedule_type=schedule_type,
            next_run=run_time.isoformat(),
            enabled=True,
            time_of_day=time_of_day,
            day_of_week=day_of_week,
            last_run=None,
        )
        self._tasks[task_id] = task
        self._save()
        logger.info(f"Created scheduled task {task_id}: {schedule_type} for #{project_name}")
        return task

    def delete_task(self, task_id: str) -> bool:
        """Delete a scheduled task.

        Args:
            task_id: The task ID to delete

        Returns:
            True if deleted, False if not found
        """
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._save()
            logger.info(f"Deleted scheduled task {task_id}")
            return True
        return False

    def list_tasks(self, chat_id: Optional[int] = None) -> list[ScheduledTask]:
        """List all scheduled tasks.

        Args:
            chat_id: Optional filter by chat ID

        Returns:
            List of ScheduledTask objects
        """
        tasks = list(self._tasks.values())
        if chat_id is not None:
            tasks = [t for t in tasks if t.chat_id == chat_id]
        return sorted(tasks, key=lambda t: t.next_run)

    async def start(self, callback: Callable[[ScheduledTask], Awaitable[None]]) -> None:
        """Start the scheduler background loop.

        Args:
            callback: Async function to call when a task is due
        """
        if self._running:
            return
        self._running = True
        self._callback = callback
        self._scheduler_task = asyncio.create_task(self._run_scheduler())
        logger.info("Scheduled task manager started")

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None
        logger.info("Scheduled task manager stopped")

    async def _run_scheduler(self) -> None:
        """Background loop that checks for due tasks."""
        while self._running:
            try:
                now = datetime.now()

                for task in list(self._tasks.values()):
                    if not task.enabled:
                        continue

                    next_run = datetime.fromisoformat(task.next_run)

                    if now >= next_run:
                        # Task is due
                        logger.info(f"Executing scheduled task {task.task_id}")

                        try:
                            if self._callback:
                                await self._callback(task)
                        except Exception as e:
                            logger.error(f"Error executing scheduled task {task.task_id}: {e}")

                        # Update last_run and calculate next_run
                        task.last_run = now.isoformat()
                        new_next = self._calculate_next_run(task, now)

                        if new_next:
                            task.next_run = new_next.isoformat()
                        else:
                            # One-time task completed - disable it
                            task.enabled = False

                        self._save()

                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(self.check_interval)

    def _calculate_next_run(
        self,
        task: ScheduledTask,
        from_time: datetime,
    ) -> Optional[datetime]:
        """Calculate the next run time for a task.

        Args:
            task: The task to calculate for
            from_time: The reference time (usually now)

        Returns:
            Next run datetime, or None for completed one-time tasks
        """
        if task.schedule_type == "once":
            return None

        # Parse time of day
        hour, minute = map(int, task.time_of_day.split(":"))

        if task.schedule_type == "daily":
            # Next day at the same time
            next_run = from_time.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if next_run <= from_time:
                next_run += timedelta(days=1)
            return next_run

        elif task.schedule_type == "weekly":
            # Next occurrence of the specified weekday
            target_day = task.day_of_week or 0
            days_ahead = target_day - from_time.weekday()

            if days_ahead <= 0:
                days_ahead += 7

            next_run = from_time.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            ) + timedelta(days=days_ahead)

            return next_run

        return None
