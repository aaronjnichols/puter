"""Scanner for Claude Code desktop sessions."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class DesktopSession:
    """Represents a Claude Code desktop session."""

    session_id: str
    project_path: str
    first_prompt: str
    modified: datetime
    message_count: int
    summary: str = ""


class DesktopSessionScanner:
    """Scans and lists Claude Code desktop sessions."""

    def __init__(self, claude_dir: Optional[Path] = None):
        """Initialize scanner.

        Args:
            claude_dir: Path to Claude projects directory.
                       Defaults to ~/.claude/projects
        """
        self.claude_dir = claude_dir or Path.home() / ".claude" / "projects"

    def get_recent_sessions(self, limit: int = 5) -> list[DesktopSession]:
        """Get the most recent Claude Code sessions across all projects.

        Args:
            limit: Maximum number of sessions to return.

        Returns:
            List of DesktopSession objects, sorted by modified time (newest first).
        """
        sessions = []

        if not self.claude_dir.exists():
            return sessions

        # Scan all sessions-index.json files
        for index_file in self.claude_dir.glob("*/sessions-index.json"):
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                for entry in data.get("entries", []):
                    # Skip subagent/sidechain sessions
                    if entry.get("isSidechain"):
                        continue

                    # Parse modified timestamp
                    modified_str = entry.get("modified", "")
                    try:
                        # Handle ISO format with timezone
                        modified = datetime.fromisoformat(
                            modified_str.replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        continue

                    sessions.append(
                        DesktopSession(
                            session_id=entry.get("sessionId", ""),
                            project_path=entry.get("projectPath", ""),
                            first_prompt=entry.get("firstPrompt", "")[:100],
                            modified=modified,
                            message_count=entry.get("messageCount", 0),
                            summary=entry.get("summary", ""),
                        )
                    )
            except (json.JSONDecodeError, IOError, KeyError):
                # Skip corrupted or unreadable files
                continue

        # Sort by modified descending (newest first)
        sessions.sort(key=lambda s: s.modified, reverse=True)
        return sessions[:limit]

    def format_time_ago(self, dt: datetime) -> str:
        """Format a datetime as a human-readable 'time ago' string.

        Args:
            dt: The datetime to format.

        Returns:
            String like "2 min ago", "1 hour ago", "yesterday", "3 days ago".
        """
        now = datetime.now(dt.tzinfo)
        diff = now - dt

        seconds = diff.total_seconds()
        minutes = seconds / 60
        hours = minutes / 60
        days = hours / 24

        if minutes < 1:
            return "just now"
        elif minutes < 60:
            mins = int(minutes)
            return f"{mins} min ago"
        elif hours < 24:
            hrs = int(hours)
            return f"{hrs} hour{'s' if hrs != 1 else ''} ago"
        elif days < 2:
            return "yesterday"
        else:
            d = int(days)
            return f"{d} days ago"

    def format_session_list(
        self,
        sessions: list[DesktopSession],
        offset: int = 0,
        has_more: bool = False,
        friendly_names: dict[str, str] = None,
    ) -> str:
        """Format a list of sessions for display.

        Args:
            sessions: List of sessions to format.
            offset: Starting number for display (0-based, displays as 1-based).
            has_more: Whether there are more sessions available.
            friendly_names: Mapping of session_id to friendly name (e.g., "obsidian").

        Returns:
            Formatted string ready to send as a message.
        """
        if not sessions:
            return "No recent Claude Code sessions found."

        if friendly_names is None:
            friendly_names = {}

        header = "Recent Claude Code sessions:" if offset == 0 else "More sessions:"
        lines = [f"{header}\n"]

        for i, session in enumerate(sessions, offset + 1):
            time_ago = self.format_time_ago(session.modified)

            # Check for friendly name tag
            friendly = friendly_names.get(session.session_id)
            name_tag = f"#{friendly} " if friendly else ""

            # Truncate path for display
            path_display = session.project_path
            if len(path_display) > 30:
                path_display = "..." + path_display[-27:]

            # Use summary if available, else first_prompt
            if session.summary:
                display_text = session.summary[:50]
                if len(session.summary) > 50:
                    display_text += "..."
            else:
                display_text = session.first_prompt[:40]
                if len(session.first_prompt) > 40:
                    display_text += "..."

            # Message count display
            msg_count = f"({session.message_count} msgs)"

            lines.append(f"{i}. {name_tag}[{time_ago}] {path_display}")
            lines.append(f"   \"{display_text}\" {msg_count}")

        lines.append("\nReply with a number to continue that session.")
        if has_more:
            lines.append("Type 'more' to see older sessions.")
        return "\n".join(lines)
