"""Telegram bot handlers for Claude Code Bridge."""

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from datetime import datetime

from .approval_handler import ApprovalHandler
from .claude_interface import ClaudeInterface, SAFE_TOOLS
from .config import Config
from .desktop_session_scanner import DesktopSessionScanner, DesktopSession
from .message_router import MessageRouter, ParsedMessage
from .output_processor import OutputProcessor
from .queue_manager import QueuedTask, QueueManager
from .scheduled_task_manager import ScheduledTaskManager, ScheduledTask
from .session_manager import SessionManager

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram bot for Claude Code Bridge."""

    def __init__(self, config: Config):
        self.config = config
        self.router = MessageRouter(config)
        self.sessions = SessionManager(config.sessions.storage_path)
        self.claude = ClaudeInterface(
            executable=config.claude_code.executable,
        )
        self.output = OutputProcessor(config.outputs_path)
        self.queue = QueueManager()
        self.approvals = ApprovalHandler()
        self.scheduler = ScheduledTaskManager(config.sessions.storage_path)
        self.scanner = DesktopSessionScanner()
        # Stores (all_sessions, current_offset, friendly_names) for pagination
        self._pending_session_select: dict[int, tuple[list[DesktopSession], int, dict[str, str]]] = {}
        self.app: Application = None

    async def start(self) -> None:
        """Start the bot."""
        self.app = (
            Application.builder()
            .token(self.config.telegram.bot_token)
            .build()
        )

        # Register handlers
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("projects", self._cmd_projects))
        self.app.add_handler(CommandHandler("new", self._cmd_new))
        self.app.add_handler(CommandHandler("skip", self._cmd_skip))
        self.app.add_handler(CommandHandler("addproject", self._cmd_addproject))
        self.app.add_handler(CommandHandler("removeproject", self._cmd_removeproject))
        self.app.add_handler(CommandHandler("project", self._cmd_project))
        self.app.add_handler(CommandHandler("schedule", self._cmd_schedule))
        self.app.add_handler(CommandHandler("tasks", self._cmd_tasks))
        self.app.add_handler(CommandHandler("deletetask", self._cmd_deletetask))
        self.app.add_handler(CommandHandler("sessions", self._cmd_sessions))
        self.app.add_handler(CommandHandler("detach", self._cmd_detach))

        # Callback handler for approval buttons
        self.app.add_handler(CallbackQueryHandler(self.approvals.handle_callback))

        # Message handler for text and photos
        self.app.add_handler(
            MessageHandler(
                filters.TEXT | filters.PHOTO,
                self._handle_message,
            )
        )

        logger.info("Starting Telegram bot...")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

        # Start the scheduled task manager
        await self.scheduler.start(self._execute_scheduled_task)

    async def stop(self) -> None:
        """Stop the bot."""
        await self.scheduler.stop()
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    def _is_authorized(self, update: Update) -> bool:
        """Check if user is authorized."""
        user = update.effective_user
        if not user:
            return False
        return self.router.is_authorized(user.id)

    async def _cmd_start(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /start command."""
        if not self._is_authorized(update):
            return

        await update.message.reply_text(
            "Claude Code Telegram Bridge\n\n"
            "Send messages to interact with Claude Code.\n"
            "Use #projectname prefix to target a specific project.\n\n"
            "Commands:\n"
            "/help - Show help\n"
            "/projects - List projects\n"
            "/new [#project] - Start new session\n"
            "/skip - Skip current task"
        )

    async def _cmd_help(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /help command."""
        if not self._is_authorized(update):
            return

        default = self.config.default_project or "(not set)"
        mode = self.config.claude_code.default_approval_mode

        chat_id = update.message.chat_id
        current_project = self.sessions.get_last_project(chat_id) or "(none)"

        await update.message.reply_text(
            "Usage:\n"
            "  Send a message to execute a task\n"
            "  Use #projectname prefix for specific project\n"
            "  Send images with caption for visual tasks\n\n"
            "Commands:\n"
            "  /projects - List configured projects\n"
            "  /project [name] - View/set current project\n"
            "  /new [#project] - Reset session, start fresh\n"
            "  /skip - Skip current errored task\n"
            "  /addproject name path - Add project\n"
            "  /removeproject name - Remove project\n\n"
            "Desktop sessions:\n"
            "  /sessions - List recent Claude Code desktop sessions\n"
            "  /detach - Detach from attached session\n\n"
            "Scheduled tasks:\n"
            "  /schedule <type> <time> #project <prompt>\n"
            "  /tasks - List scheduled tasks\n"
            "  /deletetask <id> - Delete a task\n\n"
            f"Current project: #{current_project}\n"
            f"Default project: {default}\n"
            f"Default approval mode: {mode}\n\n"
            "Approval modes:\n"
            "  safe - Auto-approve reads, ask for writes\n"
            "  ask-all - Ask for all permissions\n"
            "  auto-all - Auto-approve everything"
        )

    async def _cmd_projects(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /projects command."""
        if not self._is_authorized(update):
            return

        projects = self.router.get_project_list()
        if not projects:
            await update.message.reply_text("No projects configured.")
            return

        lines = ["Configured projects:\n"]
        for name, path, mode in projects:
            is_default = " (default)" if name == self.config.default_project else ""
            lines.append(f"  #{name}{is_default}")
            lines.append(f"    Path: {path}")
            lines.append(f"    Mode: {mode}")

        await update.message.reply_text("\n".join(lines))

    async def _cmd_new(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /new command - reset session."""
        if not self._is_authorized(update):
            return

        # Parse optional #project argument
        args = context.args or []
        project_name = None

        if args:
            arg = args[0]
            if arg.startswith("#"):
                project_name = arg[1:].lower()
            else:
                project_name = arg.lower()

        try:
            resolved_name, _ = self.config.get_project(project_name)
            self.sessions.reset_session(resolved_name)
            await update.message.reply_text(
                f"Session reset for #{resolved_name}. Next message starts fresh."
            )
        except ValueError as e:
            await update.message.reply_text(str(e))

    async def _cmd_skip(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /skip command - skip current task."""
        if not self._is_authorized(update):
            return

        # Try to skip current task for any project
        skipped = False
        for name in self.config.projects:
            if self.queue.skip_current(name):
                await update.message.reply_text(f"Skipping current task for #{name}")
                skipped = True
                break

        if not skipped:
            await update.message.reply_text("No task currently running to skip.")

    async def _cmd_addproject(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /addproject command."""
        if not self._is_authorized(update):
            return

        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text(
                "Usage: /addproject name path [mode]\n"
                "Example: /addproject webapp C:\\Projects\\webapp safe"
            )
            return

        name = args[0].lower().lstrip("#")
        path = args[1]
        mode = args[2] if len(args) > 2 else "safe"

        if mode not in ("safe", "ask-all", "auto-all"):
            await update.message.reply_text(
                f"Invalid mode '{mode}'. Use: safe, ask-all, auto-all"
            )
            return

        # Verify path exists
        if not Path(path).is_dir():
            await update.message.reply_text(f"Path does not exist: {path}")
            return

        self.config.add_project(name, path, mode)
        await update.message.reply_text(f"Added project #{name} at {path} (mode: {mode})")

    async def _cmd_removeproject(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /removeproject command."""
        if not self._is_authorized(update):
            return

        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /removeproject name")
            return

        name = args[0].lower().lstrip("#")

        if self.config.remove_project(name):
            self.sessions.reset_session(name)
            await update.message.reply_text(f"Removed project #{name}")
        else:
            await update.message.reply_text(f"Project #{name} not found")

    async def _cmd_project(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /project command - view or set current project."""
        if not self._is_authorized(update):
            return

        chat_id = update.message.chat_id
        args = context.args or []

        if not args:
            # Show current project
            last_project = self.sessions.get_last_project(chat_id)
            if last_project:
                await update.message.reply_text(f"Current project: #{last_project}")
            else:
                await update.message.reply_text(
                    "No current project set.\n"
                    "Use /project <name> to set one, or prefix a message with #projectname."
                )
            return

        # Set current project
        project_name = args[0].lower().lstrip("#")

        if project_name not in self.config.projects:
            await update.message.reply_text(
                f"Project #{project_name} not found.\n"
                "Use /projects to see available projects."
            )
            return

        self.sessions.set_last_project(chat_id, project_name)
        await update.message.reply_text(f"Current project set to #{project_name}")

    async def _cmd_schedule(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /schedule command - create a scheduled task."""
        if not self._is_authorized(update):
            return

        args = context.args or []
        if len(args) < 3:
            await update.message.reply_text(
                "Usage:\n"
                "  /schedule once <datetime> #project <prompt>\n"
                "  /schedule daily <HH:MM> #project <prompt>\n"
                "  /schedule weekly <day> <HH:MM> #project <prompt>\n\n"
                "Examples:\n"
                "  /schedule once 2024-01-15T10:00 #webapp run tests\n"
                "  /schedule daily 09:00 #api health check\n"
                "  /schedule weekly 0 09:00 #scripts backup  (0=Monday)"
            )
            return

        schedule_type = args[0].lower()
        if schedule_type not in ("once", "daily", "weekly"):
            await update.message.reply_text(
                f"Invalid schedule type '{schedule_type}'. Use: once, daily, weekly"
            )
            return

        try:
            if schedule_type == "once":
                # /schedule once <datetime> #project prompt
                run_time = datetime.fromisoformat(args[1])
                rest = " ".join(args[2:])
                time_of_day = ""
                day_of_week = None

            elif schedule_type == "daily":
                # /schedule daily <HH:MM> #project prompt
                time_of_day = args[1]
                # Validate time format
                hour, minute = map(int, time_of_day.split(":"))
                run_time = datetime.now().replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                if run_time <= datetime.now():
                    from datetime import timedelta
                    run_time += timedelta(days=1)
                rest = " ".join(args[2:])
                day_of_week = None

            else:  # weekly
                # /schedule weekly <day> <HH:MM> #project prompt
                day_of_week = int(args[1])
                if not 0 <= day_of_week <= 6:
                    raise ValueError("Day must be 0-6 (Monday-Sunday)")
                time_of_day = args[2]
                hour, minute = map(int, time_of_day.split(":"))
                # Calculate next occurrence
                from datetime import timedelta
                run_time = datetime.now().replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                days_ahead = day_of_week - run_time.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                run_time += timedelta(days=days_ahead)
                rest = " ".join(args[3:])

            # Parse #project and prompt from rest
            if not rest.startswith("#"):
                await update.message.reply_text(
                    "Please specify a project with #projectname"
                )
                return

            parts = rest.split(maxsplit=1)
            project_name = parts[0][1:].lower()  # Remove #
            prompt = parts[1] if len(parts) > 1 else ""

            if not prompt:
                await update.message.reply_text("Please provide a prompt to execute")
                return

            if project_name not in self.config.projects:
                await update.message.reply_text(f"Project #{project_name} not found")
                return

            # Create the scheduled task
            task = self.scheduler.create_task(
                chat_id=update.message.chat_id,
                project_name=project_name,
                prompt=prompt,
                schedule_type=schedule_type,
                run_time=run_time,
                time_of_day=time_of_day,
                day_of_week=day_of_week,
            )

            await update.message.reply_text(
                f"Scheduled task created (ID: {task.task_id})\n"
                f"Type: {schedule_type}\n"
                f"Project: #{project_name}\n"
                f"Next run: {task.next_run}\n"
                f"Prompt: {prompt[:50]}{'...' if len(prompt) > 50 else ''}"
            )

        except (ValueError, IndexError) as e:
            await update.message.reply_text(f"Error parsing schedule: {e}")

    async def _cmd_tasks(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /tasks command - list scheduled tasks."""
        if not self._is_authorized(update):
            return

        tasks = self.scheduler.list_tasks(chat_id=update.message.chat_id)

        if not tasks:
            await update.message.reply_text("No scheduled tasks.")
            return

        lines = ["Scheduled tasks:\n"]
        for task in tasks:
            status = "enabled" if task.enabled else "disabled"
            day_str = ""
            if task.schedule_type == "weekly" and task.day_of_week is not None:
                days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                day_str = f" ({days[task.day_of_week]})"

            lines.append(f"[{task.task_id}] {task.schedule_type}{day_str} - #{task.project_name}")
            lines.append(f"  Next: {task.next_run}")
            lines.append(f"  Prompt: {task.prompt[:40]}{'...' if len(task.prompt) > 40 else ''}")
            lines.append(f"  Status: {status}")
            lines.append("")

        await update.message.reply_text("\n".join(lines))

    async def _cmd_deletetask(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /deletetask command - delete a scheduled task."""
        if not self._is_authorized(update):
            return

        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /deletetask <task_id>")
            return

        task_id = args[0]
        if self.scheduler.delete_task(task_id):
            await update.message.reply_text(f"Deleted scheduled task {task_id}")
        else:
            await update.message.reply_text(f"Task {task_id} not found")

    async def _cmd_sessions(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /sessions command - list recent Claude Code desktop sessions."""
        if not self._is_authorized(update):
            return

        chat_id = update.message.chat_id
        # Fetch plenty of sessions for pagination (up to 50)
        all_sessions = self.scanner.get_recent_sessions(50)

        if not all_sessions:
            await update.message.reply_text("No recent Claude Code sessions found.")
            return

        # Build friendly_names mapping from tracked sessions
        # tracked is {project_name: session_id}, we need {session_id: project_name}
        tracked = self.sessions.list_sessions()
        friendly_names = {sid: name for name, sid in tracked.items()}

        # Show first 10, store all for pagination
        page_size = 10
        display_sessions = all_sessions[:page_size]
        has_more = len(all_sessions) > page_size

        # Store (all_sessions, current_offset, friendly_names)
        self._pending_session_select[chat_id] = (all_sessions, 0, friendly_names)

        # Format and send the list
        message_text = self.scanner.format_session_list(
            display_sessions, offset=0, has_more=has_more, friendly_names=friendly_names
        )
        await update.message.reply_text(message_text)

    async def _cmd_detach(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /detach command - detach from attached desktop session."""
        if not self._is_authorized(update):
            return

        chat_id = update.message.chat_id

        if self.sessions.clear_attached_session(chat_id):
            await update.message.reply_text(
                "Detached. Back to normal project mode."
            )
        else:
            await update.message.reply_text("Not attached to any desktop session.")

    async def _execute_scheduled_task(self, task: ScheduledTask) -> None:
        """Execute a scheduled task - callback for ScheduledTaskManager."""
        project_config = self.config.projects.get(task.project_name)
        if not project_config:
            logger.error(f"Scheduled task {task.task_id}: project {task.project_name} not found")
            return

        # Send notification that task is starting
        await self.app.bot.send_message(
            chat_id=task.chat_id,
            text=f"⏰ Running scheduled task ({task.task_id})\n"
                 f"Project: #{task.project_name}\n"
                 f"Prompt: {task.prompt[:50]}{'...' if len(task.prompt) > 50 else ''}",
        )

        # Get existing session
        session_id = self.sessions.get_session_id(task.project_name)

        # Execute (simplified - no approval flow for scheduled tasks, uses safe mode)
        result = await self.claude.execute(
            prompt=task.prompt,
            working_dir=project_config.path,
            session_id=session_id,
            approval_mode="safe",
            allowed_tools=None,
        )

        # Save session ID
        if result.session_id:
            self.sessions.set_session_id(task.project_name, result.session_id)

        # Process and send output
        message_text, file_path = self.output.process(
            output=result.output,
            project_name=task.project_name,
            success=result.success,
            error=result.error,
        )

        await self.app.bot.send_message(
            chat_id=task.chat_id,
            text=f"⏰ Scheduled task result ({task.task_id}):\n\n{message_text}",
        )

        if file_path:
            with open(file_path, "rb") as f:
                await self.app.bot.send_document(
                    chat_id=task.chat_id,
                    document=f,
                    filename=file_path.name,
                )

    async def _handle_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle incoming text/photo messages."""
        if not self._is_authorized(update):
            return

        message = update.message
        if not message:
            return

        # Get text from message or caption
        text = message.text or message.caption or ""
        if not text.strip():
            await message.reply_text("Please include a task description.")
            return

        chat_id = message.chat_id

        # Check if user is selecting a session (replied with just a number or 'more')
        if chat_id in self._pending_session_select:
            stripped = text.strip().lower()
            all_sessions, current_offset, friendly_names = self._pending_session_select[chat_id]
            page_size = 10

            # Handle "more" to show next page
            if stripped == "more":
                new_offset = current_offset + page_size
                if new_offset >= len(all_sessions):
                    await message.reply_text("No more sessions to show.")
                    return

                display_sessions = all_sessions[new_offset : new_offset + page_size]
                has_more = new_offset + page_size < len(all_sessions)

                # Update offset
                self._pending_session_select[chat_id] = (all_sessions, new_offset, friendly_names)

                message_text = self.scanner.format_session_list(
                    display_sessions, offset=new_offset, has_more=has_more, friendly_names=friendly_names
                )
                await message.reply_text(message_text)
                return

            # Handle number selection
            if stripped.isdigit():
                idx = int(stripped) - 1  # Convert to 0-based index
                if 0 <= idx < len(all_sessions):
                    selected = all_sessions[idx]
                    self.sessions.set_attached_session(
                        chat_id, selected.session_id, selected.project_path
                    )
                    del self._pending_session_select[chat_id]
                    await message.reply_text(
                        f"Attached to session in {selected.project_path}\n"
                        "All messages will continue this conversation.\n"
                        "Use /detach to return to normal mode."
                    )
                    return

            # Clear pending if invalid input (will process as normal message)
            del self._pending_session_select[chat_id]

        # Check if attached to a desktop session
        attached = self.sessions.get_attached_session(chat_id)
        if attached:
            await self._process_attached_message(message, text)
            return

        # Download any photos
        image_paths = []
        if message.photo:
            # Get largest photo
            photo = message.photo[-1]
            file = await context.bot.get_file(photo.file_id)

            # Download to temp file
            temp_dir = Path(tempfile.gettempdir()) / "claude_bridge"
            temp_dir.mkdir(exist_ok=True)
            image_path = temp_dir / f"{photo.file_id}.jpg"
            await file.download_to_drive(image_path)
            image_paths.append(str(image_path))

        # Get last-used project for this chat
        last_project = self.sessions.get_last_project(chat_id)

        # Parse message
        try:
            parsed = self.router.parse(text, image_paths, last_project=last_project)
        except ValueError as e:
            await message.reply_text(str(e))
            return

        # Save this as the last-used project
        self.sessions.set_last_project(chat_id, parsed.project_name)

        # Create queued task
        task = QueuedTask(
            project_name=parsed.project_name,
            prompt=parsed.task,
            image_paths=parsed.image_paths,
            message_id=message.message_id,
            chat_id=message.chat_id,
            callback=lambda: None,  # Set by processor
        )

        # Enqueue task
        position = await self.queue.enqueue(task, self._process_task)

        # Send queue position
        status_msg = self.output.format_queue_position(position, parsed.project_name)
        await message.reply_text(status_msg)

    async def _process_task(self, task: QueuedTask) -> None:
        """Process a queued task."""
        project_name = task.project_name
        project_config = self.config.projects[project_name]

        # Build prompt with image references
        prompt = task.prompt
        if task.image_paths:
            paths_str = ", ".join(task.image_paths)
            prompt = f"{prompt}\n\n[Images attached: {paths_str}]"

        # Get existing session
        session_id = self.sessions.get_session_id(project_name)

        # Track allowed tools for ask-all mode
        allowed_tools: list[str] = []

        while True:
            # Execute Claude
            result = await self.claude.execute(
                prompt=prompt,
                working_dir=project_config.path,
                session_id=session_id,
                approval_mode=project_config.approval_mode,
                allowed_tools=allowed_tools if allowed_tools else None,
            )

            # Save session ID
            if result.session_id:
                self.sessions.set_session_id(project_name, result.session_id)

            # Check for permission denials (ask-all mode)
            if result.permission_denials and project_config.approval_mode == "ask-all":
                denial = result.permission_denials[0]
                tool_name = denial.get("tool", "unknown")
                tool_input = denial.get("input", {})

                # Send approval request
                approval_msg = self.approvals.format_approval_message(
                    tool_name, tool_input, project_name
                )
                sent = await self.app.bot.send_message(
                    chat_id=task.chat_id,
                    text=approval_msg,
                    reply_markup=self.approvals.create_keyboard(),
                )

                # Wait for approval
                approved = await self.approvals.request_approval(
                    project_name=project_name,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    message_id=sent.message_id,
                    chat_id=task.chat_id,
                )

                if approved:
                    # Add tool to allowed list and re-run
                    allowed_tools.append(tool_name)
                    continue
                else:
                    # Report denial and stop
                    await self.app.bot.send_message(
                        chat_id=task.chat_id,
                        text=f"Permission denied for {tool_name}. Task stopped.",
                    )
                    return

            # No more permission requests, process output
            break

        # Process and send output
        message_text, file_path = self.output.process(
            output=result.output,
            project_name=project_name,
            success=result.success,
            error=result.error,
        )

        # Send result
        await self.app.bot.send_message(
            chat_id=task.chat_id,
            text=message_text,
        )

        # Send file if created
        if file_path:
            with open(file_path, "rb") as f:
                await self.app.bot.send_document(
                    chat_id=task.chat_id,
                    document=f,
                    filename=file_path.name,
                )

    async def _process_attached_message(self, message, text: str) -> None:
        """Process a message for an attached desktop session."""
        chat_id = message.chat_id
        attached = self.sessions.get_attached_session(chat_id)

        if not attached:
            # Should not happen, but handle gracefully
            await message.reply_text("Not attached to any session.")
            return

        session_id = attached["session_id"]
        project_path = attached["project_path"]

        # Send processing indicator
        await message.reply_text(f"[Attached: {project_path}]\nProcessing...")

        # Execute with --resume pointing to the desktop session
        result = await self.claude.execute(
            prompt=text,
            working_dir=project_path,
            session_id=session_id,
            approval_mode="safe",  # Use safe mode for attached sessions
            allowed_tools=None,
        )

        # Check if session was not found (file deleted, etc.)
        if result.error and "session" in result.error.lower():
            self.sessions.clear_attached_session(chat_id)
            await message.reply_text(
                f"Session not found. Detached from {project_path}.\n"
                "Use /sessions to select a new session."
            )
            return

        # Process and send output
        # Use a generic name since this is a desktop session
        display_name = Path(project_path).name
        message_text, file_path = self.output.process(
            output=result.output,
            project_name=display_name,
            success=result.success,
            error=result.error,
        )

        # Send result
        await self.app.bot.send_message(
            chat_id=chat_id,
            text=message_text,
        )

        # Send file if created
        if file_path:
            with open(file_path, "rb") as f:
                await self.app.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=file_path.name,
                )
