# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Telegram bridge for Claude Code - it allows controlling Claude Code on a Windows PC via Telegram messages from a phone. The bridge spawns Claude Code CLI as a subprocess, parses its JSON stream output, and relays results back via Telegram.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bridge
python bridge.py

# Or use the batch file
start.bat
```

## Architecture

The flow is: **Telegram message → MessageRouter → QueueManager → ClaudeInterface → Claude CLI subprocess → OutputProcessor → Telegram response**

Key architectural decisions:

1. **Per-project queues**: Each project has its own async queue. Tasks for the same project run sequentially; different projects can process in parallel.

2. **Session continuity**: Sessions are persisted to JSON files in `./sessions/`. The bridge uses Claude Code's `--resume <session_id>` flag to maintain conversation context across messages.

3. **Approval modes**: Three modes control how permissions are handled:
   - `safe`: Uses `--allowedTools` with read-only tools (Read, Glob, Grep, etc.)
   - `ask-all`: No flags; parses `permission_denials` from result and prompts user via inline buttons
   - `auto-all`: Uses `--dangerously-skip-permissions`

4. **Approval flow for ask-all**: When Claude returns `permission_denials`, the bridge sends an inline keyboard to Telegram, waits for user response via `asyncio.Event`, then re-runs with the tool added to `--allowedTools`.

## Module Responsibilities

- **telegram_bot.py**: Central orchestrator. Handles all Telegram commands, downloads photos, enqueues tasks, and runs the `_process_task` loop that calls ClaudeInterface and handles approvals.
- **claude_interface.py**: Builds CLI commands, spawns subprocess, parses JSON stream. The `SAFE_TOOLS` list defines which tools are auto-approved in safe mode.
- **queue_manager.py**: Async queues per project with auto-starting processor tasks. Supports skip signals for error recovery.
- **approval_handler.py**: Manages pending approvals as a dict keyed by `chat_id:message_id`. Uses asyncio.Event to block until user responds to inline buttons.

## Claude Code CLI Flags Used

```bash
claude -p --output-format stream-json --verbose "prompt"  # Base command
claude --resume <session-id> ...                          # Session continuity
claude --dangerously-skip-permissions ...                 # Auto-all mode
claude --allowedTools "Read,Glob,Grep,..." ...            # Safe/ask-all mode
```

## JSON Stream Format

The bridge expects these message types from Claude CLI stdout:
- `{"type": "system", "subtype": "init", "session_id": "..."}` - Extract session_id
- `{"type": "assistant", "message": {"content": [...]}}` - Extract text content
- `{"type": "result", "result": "...", "permission_denials": [...]}` - Final result
