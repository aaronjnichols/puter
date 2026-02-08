"""Claude Code CLI interface for Telegram Bridge."""

import asyncio
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

# Safe read-only tools for "safe" approval mode
SAFE_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Task",
    "Bash(git status)",
    "Bash(git diff)",
    "Bash(git log)",
]


@dataclass
class ClaudeResult:
    """Result from Claude Code execution."""
    success: bool
    output: str
    session_id: Optional[str] = None
    permission_denials: list[dict] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class StreamUpdate:
    """Update during Claude Code streaming."""
    type: str  # init, assistant, result, error
    content: str
    session_id: Optional[str] = None
    raw_data: Optional[dict] = None


class ClaudeInterface:
    """Interface to Claude Code CLI."""

    def __init__(
        self,
        executable: str = "claude",
        timeout: int = 1800,
    ):
        self.executable = executable
        self.timeout = timeout
        self._is_windows = sys.platform == "win32"

    def _build_command(
        self,
        session_id: Optional[str] = None,
        approval_mode: str = "safe",
        allowed_tools: Optional[list[str]] = None,
    ) -> list[str]:
        """Build Claude CLI command (prompt passed via stdin)."""
        cmd = [self.executable]

        # Resume session if exists
        if session_id:
            cmd.extend(["--resume", session_id])

        # Output format - use -p for print mode, prompt comes from stdin
        cmd.extend(["-p", "--output-format", "stream-json", "--verbose"])

        # Approval mode handling
        if approval_mode == "auto-all":
            cmd.append("--dangerously-skip-permissions")
        elif approval_mode == "safe":
            # Use safe read-only tools
            tools = allowed_tools or SAFE_TOOLS
            cmd.extend(["--allowedTools", ",".join(tools)])
        elif approval_mode == "ask-all" and allowed_tools:
            # Only include explicitly allowed tools
            cmd.extend(["--allowedTools", ",".join(allowed_tools)])
        # ask-all with no allowed_tools: no flags, will return permission_denials

        return cmd

    async def execute(
        self,
        prompt: str,
        working_dir: str,
        session_id: Optional[str] = None,
        approval_mode: str = "safe",
        allowed_tools: Optional[list[str]] = None,
    ) -> ClaudeResult:
        """Execute a prompt and return the full result."""
        cmd = self._build_command(
            session_id=session_id,
            approval_mode=approval_mode,
            allowed_tools=allowed_tools,
        )

        logger.info(f"Executing: {' '.join(cmd)}")
        logger.info(f"Prompt: {prompt[:100]}...")
        logger.info(f"Working dir: {working_dir}")

        try:
            if self._is_windows:
                # On Windows, use shell=True to resolve .cmd files from PATH
                cmd_str = subprocess.list2cmdline(cmd)
                logger.info(f"Windows command: {cmd_str}")
                process = await asyncio.create_subprocess_shell(
                    cmd_str,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=working_dir,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=working_dir,
                )

            # Pass prompt via stdin
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=prompt.encode("utf-8")),
                timeout=self.timeout,
            )

            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")

            if stderr_text:
                logger.warning(f"Claude stderr: {stderr_text}")

            logger.info(f"Claude stdout length: {len(stdout_text)}")

            return self._parse_output(stdout_text)

        except asyncio.TimeoutError:
            return ClaudeResult(
                success=False,
                output="",
                error=f"Command timed out after {self.timeout} seconds",
            )
        except Exception as e:
            return ClaudeResult(
                success=False,
                output="",
                error=str(e),
            )

    async def stream(
        self,
        prompt: str,
        working_dir: str,
        session_id: Optional[str] = None,
        approval_mode: str = "safe",
        allowed_tools: Optional[list[str]] = None,
    ) -> AsyncIterator[StreamUpdate]:
        """Stream updates from Claude Code execution."""
        cmd = self._build_command(
            session_id=session_id,
            approval_mode=approval_mode,
            allowed_tools=allowed_tools,
        )

        logger.info(f"Streaming: {' '.join(cmd)}")

        try:
            if self._is_windows:
                process = await asyncio.create_subprocess_shell(
                    subprocess.list2cmdline(cmd),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=working_dir,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=working_dir,
                )

            # Write prompt to stdin and close it
            process.stdin.write(prompt.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()

            current_session_id = None

            async for line in process.stdout:
                line_text = line.decode("utf-8", errors="replace").strip()
                if not line_text:
                    continue

                try:
                    data = json.loads(line_text)
                    update = self._parse_stream_line(data)
                    if update.session_id:
                        current_session_id = update.session_id
                    yield update
                except json.JSONDecodeError:
                    # Non-JSON output, yield as raw content
                    yield StreamUpdate(type="raw", content=line_text)

            await process.wait()

        except Exception as e:
            yield StreamUpdate(type="error", content=str(e))

    def _parse_stream_line(self, data: dict) -> StreamUpdate:
        """Parse a single JSON line from stream output."""
        msg_type = data.get("type", "unknown")
        session_id = data.get("session_id")

        if msg_type == "system":
            subtype = data.get("subtype", "")
            return StreamUpdate(
                type="init" if subtype == "init" else "system",
                content=f"[{subtype}]",
                session_id=session_id,
                raw_data=data,
            )

        elif msg_type == "assistant":
            content = self._extract_assistant_content(data)
            return StreamUpdate(
                type="assistant",
                content=content,
                session_id=session_id,
                raw_data=data,
            )

        elif msg_type == "result":
            result_text = data.get("result", "")
            return StreamUpdate(
                type="result",
                content=result_text,
                session_id=session_id,
                raw_data=data,
            )

        else:
            return StreamUpdate(
                type=msg_type,
                content=str(data),
                session_id=session_id,
                raw_data=data,
            )

    def _extract_assistant_content(self, data: dict) -> str:
        """Extract text content from assistant message."""
        message = data.get("message", {})
        content_blocks = message.get("content", [])

        text_parts = []
        for block in content_blocks:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tool_name = block.get("name", "unknown")
                    text_parts.append(f"[Using tool: {tool_name}]")

        return "\n".join(text_parts)

    def _parse_output(self, output: str) -> ClaudeResult:
        """Parse complete Claude output into result."""
        lines = output.strip().split("\n")

        session_id = None
        result_text = ""
        permission_denials = []
        last_assistant_content = ""

        for line in lines:
            if not line.strip():
                continue

            try:
                data = json.loads(line)
                msg_type = data.get("type")

                # Extract session ID from any message
                if "session_id" in data:
                    session_id = data["session_id"]

                if msg_type == "assistant":
                    last_assistant_content = self._extract_assistant_content(data)

                elif msg_type == "result":
                    result_text = data.get("result", "")
                    permission_denials = data.get("permission_denials", [])

            except json.JSONDecodeError:
                # Non-JSON line, might be an error
                continue

        # Use result text if available, otherwise last assistant content
        final_output = result_text or last_assistant_content

        return ClaudeResult(
            success=True,
            output=final_output,
            session_id=session_id,
            permission_denials=permission_denials,
        )
