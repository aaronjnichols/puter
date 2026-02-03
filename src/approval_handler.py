"""Permission approval handling for Claude Code Telegram Bridge."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


@dataclass
class PendingApproval:
    """A pending permission approval request."""
    project_name: str
    tool_name: str
    tool_input: dict
    message_id: int
    chat_id: int
    event: asyncio.Event
    approved: Optional[bool] = None


class ApprovalHandler:
    """Handles permission approval via Telegram inline buttons."""

    CALLBACK_APPROVE = "approve"
    CALLBACK_DENY = "deny"

    def __init__(self):
        self._pending: dict[str, PendingApproval] = {}  # key: "chat_id:message_id"

    def _make_key(self, chat_id: int, message_id: int) -> str:
        """Create unique key for pending approval."""
        return f"{chat_id}:{message_id}"

    def create_keyboard(self) -> InlineKeyboardMarkup:
        """Create approval inline keyboard."""
        keyboard = [
            [
                InlineKeyboardButton("Allow", callback_data=self.CALLBACK_APPROVE),
                InlineKeyboardButton("Deny", callback_data=self.CALLBACK_DENY),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def request_approval(
        self,
        project_name: str,
        tool_name: str,
        tool_input: dict,
        message_id: int,
        chat_id: int,
    ) -> bool:
        """Wait for user approval. Returns True if approved."""
        key = self._make_key(chat_id, message_id)

        approval = PendingApproval(
            project_name=project_name,
            tool_name=tool_name,
            tool_input=tool_input,
            message_id=message_id,
            chat_id=chat_id,
            event=asyncio.Event(),
        )

        self._pending[key] = approval

        try:
            # Wait for user response (with timeout)
            await asyncio.wait_for(approval.event.wait(), timeout=300)
            return approval.approved is True
        except asyncio.TimeoutError:
            logger.warning(f"Approval timeout for {tool_name} in {project_name}")
            return False
        finally:
            self._pending.pop(key, None)

    async def handle_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle inline button callback."""
        query = update.callback_query
        if not query:
            return

        await query.answer()

        message = query.message
        if not message:
            return

        key = self._make_key(message.chat_id, message.message_id)
        approval = self._pending.get(key)

        if not approval:
            await query.edit_message_text("This approval request has expired.")
            return

        if query.data == self.CALLBACK_APPROVE:
            approval.approved = True
            status = "Approved"
        else:
            approval.approved = False
            status = "Denied"

        # Update message with result
        original_text = message.text or ""
        await query.edit_message_text(
            f"{original_text}\n\n{status}",
            reply_markup=None,
        )

        # Signal waiting coroutine
        approval.event.set()

    def format_approval_message(
        self,
        tool_name: str,
        tool_input: dict,
        project_name: str,
    ) -> str:
        """Format the approval request message."""
        input_str = str(tool_input)
        if len(input_str) > 800:
            input_str = input_str[:800] + "..."

        return (
            f"Permission requested for #{project_name}\n\n"
            f"Tool: {tool_name}\n"
            f"Input:\n{input_str}"
        )

    def has_pending(self, chat_id: int) -> bool:
        """Check if there are pending approvals for a chat."""
        prefix = f"{chat_id}:"
        return any(k.startswith(prefix) for k in self._pending)
