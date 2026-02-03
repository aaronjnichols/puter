"""Session persistence for Claude Code Telegram Bridge."""

import json
from pathlib import Path
from typing import Optional


class SessionManager:
    """Manages session ID persistence per project."""

    def __init__(self, storage_path: str):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, str] = {}
        self._load_all()

    def _session_file(self, project_name: str) -> Path:
        """Get path to session file for project."""
        return self.storage_path / f"{project_name}.json"

    def _load_all(self) -> None:
        """Load all existing sessions from disk."""
        for file in self.storage_path.glob("*.json"):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    project_name = file.stem
                    if "session_id" in data:
                        self._sessions[project_name] = data["session_id"]
            except (json.JSONDecodeError, IOError):
                # Skip corrupted files
                pass

    def get_session_id(self, project_name: str) -> Optional[str]:
        """Get session ID for a project, if exists."""
        return self._sessions.get(project_name)

    def set_session_id(self, project_name: str, session_id: str) -> None:
        """Store session ID for a project."""
        self._sessions[project_name] = session_id

        session_file = self._session_file(project_name)
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump({"session_id": session_id}, f)

    def reset_session(self, project_name: str) -> bool:
        """Reset session for a project. Returns True if session existed."""
        if project_name in self._sessions:
            del self._sessions[project_name]

            session_file = self._session_file(project_name)
            if session_file.exists():
                session_file.unlink()
            return True
        return False

    def list_sessions(self) -> dict[str, str]:
        """Get all active sessions."""
        return dict(self._sessions)
