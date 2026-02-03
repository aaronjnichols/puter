"""Configuration management for Claude Code Telegram Bridge."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import json


@dataclass
class TelegramConfig:
    """Telegram bot configuration."""
    bot_token: str
    authorized_user_id: int


@dataclass
class ProjectConfig:
    """Per-project configuration."""
    path: str
    approval_mode: str = "safe"  # safe, ask-all, auto-all


@dataclass
class ClaudeCodeConfig:
    """Claude Code CLI configuration."""
    executable: str = "claude"
    default_approval_mode: str = "safe"


@dataclass
class SessionsConfig:
    """Session storage configuration."""
    storage_path: str = "./sessions"


@dataclass
class Config:
    """Main configuration container."""
    telegram: TelegramConfig
    projects: dict[str, ProjectConfig] = field(default_factory=dict)
    default_project: Optional[str] = None
    claude_code: ClaudeCodeConfig = field(default_factory=ClaudeCodeConfig)
    sessions: SessionsConfig = field(default_factory=SessionsConfig)
    outputs_path: str = "./outputs"
    _config_path: Optional[Path] = field(default=None, repr=False)

    @classmethod
    def load(cls, path: str | Path = "config.json") -> "Config":
        """Load configuration from JSON file."""
        config_path = Path(path)
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        telegram = TelegramConfig(
            bot_token=data["telegram"]["bot_token"],
            authorized_user_id=data["telegram"]["authorized_user_id"],
        )

        projects = {}
        for name, proj_data in data.get("projects", {}).items():
            projects[name] = ProjectConfig(
                path=proj_data["path"],
                approval_mode=proj_data.get("approval_mode", "safe"),
            )

        claude_data = data.get("claude_code", {})
        claude_code = ClaudeCodeConfig(
            executable=claude_data.get("executable", "claude"),
            default_approval_mode=claude_data.get("default_approval_mode", "safe"),
        )

        sessions_data = data.get("sessions", {})
        sessions = SessionsConfig(
            storage_path=sessions_data.get("storage_path", "./sessions"),
        )

        config = cls(
            telegram=telegram,
            projects=projects,
            default_project=data.get("default_project"),
            claude_code=claude_code,
            sessions=sessions,
            outputs_path=data.get("outputs_path", "./outputs"),
            _config_path=config_path,
        )

        # Ensure directories exist
        Path(config.sessions.storage_path).mkdir(parents=True, exist_ok=True)
        Path(config.outputs_path).mkdir(parents=True, exist_ok=True)

        return config

    def save(self) -> None:
        """Save configuration to JSON file."""
        if self._config_path is None:
            raise ValueError("No config path set")

        data = {
            "telegram": {
                "bot_token": self.telegram.bot_token,
                "authorized_user_id": self.telegram.authorized_user_id,
            },
            "default_project": self.default_project,
            "projects": {
                name: {
                    "path": proj.path,
                    "approval_mode": proj.approval_mode,
                }
                for name, proj in self.projects.items()
            },
            "claude_code": {
                "executable": self.claude_code.executable,
                "default_approval_mode": self.claude_code.default_approval_mode,
            },
            "sessions": {
                "storage_path": self.sessions.storage_path,
            },
            "outputs_path": self.outputs_path,
        }

        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get_project(self, name: Optional[str] = None) -> tuple[str, ProjectConfig]:
        """Get project by name or default project.

        Returns:
            Tuple of (project_name, project_config)

        Raises:
            ValueError: If project not found or no default set.
        """
        if name is None:
            name = self.default_project

        if name is None:
            raise ValueError("No project specified and no default project set")

        if name not in self.projects:
            raise ValueError(f"Project '{name}' not found")

        return name, self.projects[name]

    def add_project(self, name: str, path: str, approval_mode: str = "safe") -> None:
        """Add a new project configuration."""
        self.projects[name] = ProjectConfig(path=path, approval_mode=approval_mode)
        self.save()

    def remove_project(self, name: str) -> bool:
        """Remove a project configuration. Returns True if removed."""
        if name in self.projects:
            del self.projects[name]
            if self.default_project == name:
                self.default_project = None
            self.save()
            return True
        return False
