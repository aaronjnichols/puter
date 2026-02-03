"""Task queue management for Claude Code Telegram Bridge."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


@dataclass
class QueuedTask:
    """A task waiting in queue."""
    project_name: str
    prompt: str
    image_paths: list[str]
    message_id: int
    chat_id: int
    callback: Callable[..., Coroutine[Any, Any, None]]


class QueueManager:
    """Manages per-project task queues."""

    def __init__(self):
        self._queues: dict[str, asyncio.Queue[QueuedTask]] = {}
        self._processors: dict[str, asyncio.Task] = {}
        self._current_tasks: dict[str, Optional[QueuedTask]] = {}
        self._skip_events: dict[str, asyncio.Event] = {}

    def _get_queue(self, project_name: str) -> asyncio.Queue[QueuedTask]:
        """Get or create queue for project."""
        if project_name not in self._queues:
            self._queues[project_name] = asyncio.Queue()
        return self._queues[project_name]

    async def enqueue(
        self,
        task: QueuedTask,
        processor: Callable[[QueuedTask], Coroutine[Any, Any, None]],
    ) -> int:
        """Add task to queue. Returns queue position (0 = processing now)."""
        project_name = task.project_name
        queue = self._get_queue(project_name)

        # Get position before adding
        position = queue.qsize()
        if project_name in self._current_tasks and self._current_tasks[project_name]:
            position += 1  # Account for currently processing task

        await queue.put(task)

        # Start processor if not running
        if project_name not in self._processors or self._processors[project_name].done():
            self._processors[project_name] = asyncio.create_task(
                self._process_queue(project_name, processor)
            )
            self._skip_events[project_name] = asyncio.Event()

        return position

    async def _process_queue(
        self,
        project_name: str,
        processor: Callable[[QueuedTask], Coroutine[Any, Any, None]],
    ) -> None:
        """Process tasks in queue sequentially."""
        queue = self._get_queue(project_name)

        while True:
            try:
                # Wait for next task
                task = await asyncio.wait_for(queue.get(), timeout=60.0)
                self._current_tasks[project_name] = task

                try:
                    await processor(task)
                except Exception as e:
                    logger.error(f"Error processing task for {project_name}: {e}")
                finally:
                    self._current_tasks[project_name] = None
                    queue.task_done()

            except asyncio.TimeoutError:
                # No tasks for 60 seconds, stop processor
                if queue.empty():
                    logger.info(f"Queue processor for {project_name} stopping (idle)")
                    break

    def skip_current(self, project_name: str) -> bool:
        """Signal to skip current task. Returns True if there was a task."""
        if project_name in self._skip_events:
            self._skip_events[project_name].set()
            return project_name in self._current_tasks and self._current_tasks[project_name] is not None
        return False

    def get_skip_event(self, project_name: str) -> Optional[asyncio.Event]:
        """Get skip event for project."""
        return self._skip_events.get(project_name)

    def get_queue_size(self, project_name: str) -> int:
        """Get current queue size for project."""
        if project_name not in self._queues:
            return 0
        return self._queues[project_name].qsize()

    def get_current_task(self, project_name: str) -> Optional[QueuedTask]:
        """Get currently processing task for project."""
        return self._current_tasks.get(project_name)

    def get_all_queue_sizes(self) -> dict[str, int]:
        """Get queue sizes for all projects."""
        return {name: q.qsize() for name, q in self._queues.items()}
