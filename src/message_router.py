"""Message parsing and routing for Claude Code Telegram Bridge."""

import re
from dataclasses import dataclass
from typing import Optional

from .config import Config, ProjectConfig


@dataclass
class ParsedMessage:
    """Parsed message with project and task."""
    project_name: str
    project_config: ProjectConfig
    task: str
    image_paths: list[str]


class MessageRouter:
    """Parses messages and routes to appropriate projects."""

    # Match #projectname at the start of message
    PROJECT_PATTERN = re.compile(r"^#(\w+)\s+(.+)$", re.DOTALL)

    def __init__(self, config: Config):
        self.config = config

    def is_authorized(self, user_id: int) -> bool:
        """Check if user is authorized."""
        return user_id == self.config.telegram.authorized_user_id

    def parse(
        self,
        text: str,
        image_paths: Optional[list[str]] = None,
    ) -> ParsedMessage:
        """Parse a message and resolve the target project.

        Args:
            text: Message text, optionally with #project prefix
            image_paths: Paths to downloaded images

        Returns:
            ParsedMessage with resolved project and task

        Raises:
            ValueError: If project not found or no default set
        """
        image_paths = image_paths or []

        # Try to match #project prefix
        match = self.PROJECT_PATTERN.match(text.strip())

        if match:
            project_name = match.group(1).lower()
            task = match.group(2).strip()
        else:
            project_name = None
            task = text.strip()

        # Resolve project (may raise ValueError)
        resolved_name, project_config = self.config.get_project(project_name)

        return ParsedMessage(
            project_name=resolved_name,
            project_config=project_config,
            task=task,
            image_paths=image_paths,
        )

    def get_project_list(self) -> list[tuple[str, str, str]]:
        """Get list of configured projects.

        Returns:
            List of (name, path, approval_mode) tuples
        """
        return [
            (name, proj.path, proj.approval_mode)
            for name, proj in self.config.projects.items()
        ]
