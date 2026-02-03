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

from .approval_handler import ApprovalHandler
from .claude_interface import ClaudeInterface, SAFE_TOOLS
from .config import Config
from .message_router import MessageRouter, ParsedMessage
from .output_processor import OutputProcessor
from .queue_manager import QueuedTask, QueueManager
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

    async def stop(self) -> None:
        """Stop the bot."""
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

        await update.message.reply_text(
            "Usage:\n"
            "  Send a message to execute a task\n"
            "  Use #projectname prefix for specific project\n"
            "  Send images with caption for visual tasks\n\n"
            "Commands:\n"
            "  /projects - List configured projects\n"
            "  /new [#project] - Reset session, start fresh\n"
            "  /skip - Skip current errored task\n"
            "  /addproject name path - Add project\n"
            "  /removeproject name - Remove project\n\n"
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

        # Parse message
        try:
            parsed = self.router.parse(text, image_paths)
        except ValueError as e:
            await message.reply_text(str(e))
            return

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
