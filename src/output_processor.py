"""Output processing for Claude Code Telegram Bridge."""

from datetime import datetime
from pathlib import Path
from typing import Optional


class OutputProcessor:
    """Processes and formats Claude output for Telegram."""

    MAX_MESSAGE_LENGTH = 4000  # Telegram limit is 4096, leave margin

    def __init__(self, outputs_path: str):
        self.outputs_path = Path(outputs_path)
        self.outputs_path.mkdir(parents=True, exist_ok=True)

    def process(
        self,
        output: str,
        project_name: str,
        success: bool = True,
        error: Optional[str] = None,
    ) -> tuple[str, Optional[Path]]:
        """Process output for Telegram.

        Returns:
            Tuple of (message_text, optional_file_path)
            If output is short, returns (output, None)
            If output is long, returns (summary, file_path)
        """
        if error:
            return self._format_error(error), None

        if not output:
            return self._format_empty(success), None

        # Check if output fits in message
        if len(output) <= self.MAX_MESSAGE_LENGTH:
            return self._format_success(output), None

        # Save to file and return summary
        file_path = self._save_to_file(output, project_name)
        summary = self._create_summary(output)

        return summary, file_path

    def _format_success(self, output: str) -> str:
        """Format successful output."""
        return output

    def _format_error(self, error: str) -> str:
        """Format error message."""
        return f"Error: {error}"

    def _format_empty(self, success: bool) -> str:
        """Format empty output message."""
        if success:
            return "Task completed (no output)"
        return "Task failed with no output"

    def _save_to_file(self, output: str, project_name: str) -> Path:
        """Save long output to markdown file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{project_name}_{timestamp}.md"
        file_path = self.outputs_path / filename

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# Claude Output - {project_name}\n\n")
            f.write(f"Generated: {datetime.now().isoformat()}\n\n")
            f.write("---\n\n")
            f.write(output)

        return file_path

    def _create_summary(self, output: str) -> str:
        """Create summary for long output."""
        # Get first few lines as preview
        lines = output.split("\n")
        preview_lines = lines[:10]
        preview = "\n".join(preview_lines)

        if len(preview) > 500:
            preview = preview[:500] + "..."

        char_count = len(output)
        line_count = len(lines)

        summary = (
            f"Output saved to file ({char_count:,} chars, {line_count} lines)\n\n"
            f"Preview:\n{preview}"
        )

        return summary

    def format_queue_position(self, position: int, project_name: str) -> str:
        """Format queue position message."""
        if position == 0:
            return f"Processing task for #{project_name}..."
        return f"Queued for #{project_name} (position {position})"

    def format_permission_request(
        self,
        tool_name: str,
        tool_input: dict,
        project_name: str,
    ) -> str:
        """Format permission request message."""
        # Truncate large inputs
        input_str = str(tool_input)
        if len(input_str) > 500:
            input_str = input_str[:500] + "..."

        return (
            f"Permission requested for #{project_name}\n\n"
            f"Tool: {tool_name}\n"
            f"Input: {input_str}"
        )
