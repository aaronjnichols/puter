# Claude Code Telegram Bridge - Project Specification

## Overview

A Python-based bridge that allows you to control Claude Code on your Windows machine via Telegram messages from your phone. Send tasks, receive responses, and manage projects—all without being at your computer.

---

## Core Features

### 1. Telegram Bot Interface

- **Platform:** Telegram Bot API
- **Authentication:** Single authorized Telegram user ID (your account only)
- **Communication:** Bidirectional - send tasks, receive results and approval requests

### 2. Named Projects

Projects are pre-configured directories that Claude Code can work in.

**Project Syntax:**
```
#projectname your task here
```

**Examples:**
```
#webapp Fix the login button not responding to clicks
#api Add rate limiting to the /users endpoint
#scripts Create a backup script for the database
```

**Configuration Methods:**
1. **JSON config file** - Edit `config.json` for initial setup
2. **Telegram commands** - Quick changes from your phone:
   - `/addproject name C:\path\to\project` - Add a new project
   - `/removeproject name` - Remove a project
   - `/projects` - List all configured projects

**Default Project:**
- Set a default project in config
- Messages without `#project` prefix go to default project
- If no default set and no prefix, bot asks which project

### 3. Session Management

- **Persistent sessions** per project - context maintained between messages
- Sessions stored locally, survive bot restarts
- **Commands:**
  - `/new` - Start a fresh session for current project
  - `/new #projectname` - Start fresh session for specific project

### 4. Approval System

Claude Code sometimes needs permission for file edits, bash commands, etc.

**Modes (configurable per-project or globally):**

| Mode | Behavior |
|------|----------|
| `safe` (default) | Auto-approve reads/safe operations, ask for writes/destructive ops |
| `ask-all` | Ask approval for everything via Telegram |
| `auto-all` | Auto-approve everything (uses `--dangerously-skip-permissions`) |

**Approval Flow:**
1. Claude Code requests permission
2. Bot sends you a Telegram message: "Claude wants to edit `src/app.js`. Allow? [Yes] [No]"
3. You tap Yes or No
4. Bot continues or cancels based on your response

### 5. Output Handling

**For outputs under 4000 characters:**
- Send directly as Telegram message

**For long outputs:**
1. Claude Code summarizes the key points
2. Full output saved as `.md` file
3. File uploaded to Telegram for download

### 6. Image Support

Send images/screenshots directly in Telegram:
- Bug screenshots
- UI mockups
- Error messages
- Design references

Images are passed to Claude Code's multimodal capabilities.

### 7. Message Queue

When you send multiple messages quickly:
- Messages queued in order received
- Each processed sequentially after previous completes
- Bot acknowledges: "Queued (position 2)"

### 8. Notifications

**Minimal notification mode:**
- ✅ Task completed (with summary)
- ❓ Approval needed
- ❌ Error occurred
- 📥 Queued confirmation (when busy)

*No verbose progress updates or "starting task" messages.*

### 9. Error Handling

When Claude Code encounters an error:
1. Bot sends error details to you
2. Waits for your guidance before proceeding
3. You can reply with instructions or send `/skip` to move to next queued task

---

## Commands Reference

| Command | Description |
|---------|-------------|
| `/new` | Start fresh session for current/default project |
| `/new #project` | Start fresh session for specific project |
| `/addproject name path` | Add new project |
| `/removeproject name` | Remove a project |
| `/projects` | List all projects with paths |
| `/skip` | Skip current error, proceed to next queued message |
| `/help` | Show available commands |

---

## Configuration

### config.json

```json
{
  "telegram": {
    "bot_token": "YOUR_BOT_TOKEN_HERE",
    "authorized_user_id": 123456789
  },
  "default_project": "webapp",
  "projects": {
    "webapp": {
      "path": "C:\\Projects\\my-webapp",
      "approval_mode": "safe"
    },
    "api": {
      "path": "C:\\Projects\\backend-api",
      "approval_mode": "auto-all"
    },
    "scripts": {
      "path": "C:\\Scripts",
      "approval_mode": "ask-all"
    }
  },
  "claude_code": {
    "executable": "claude",
    "default_approval_mode": "safe"
  },
  "sessions": {
    "storage_path": "./sessions"
  }
}
```

### Getting Your Telegram User ID

1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. It replies with your user ID
3. Add to `authorized_user_id` in config

### Creating a Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow prompts
3. Copy the bot token to `bot_token` in config

---

## Architecture

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│                 │         │                  │         │                 │
│  Your Phone     │◄───────►│  Telegram API    │◄───────►│  Bridge Script  │
│  (Telegram App) │         │  (Cloud)         │         │  (Your PC)      │
│                 │         │                  │         │                 │
└─────────────────┘         └──────────────────┘         └────────┬────────┘
                                                                  │
                                                                  ▼
                                                         ┌─────────────────┐
                                                         │                 │
                                                         │  Claude Code    │
                                                         │  (CLI)          │
                                                         │                 │
                                                         └─────────────────┘
```

### Components

1. **Telegram Poller** - Long-polls Telegram API for new messages
2. **Message Router** - Parses project tags, routes to correct handler
3. **Session Manager** - Maintains Claude Code sessions per project
4. **Claude Code Interface** - Spawns and communicates with Claude Code process
5. **Approval Handler** - Intercepts permission requests, sends to Telegram
6. **Output Processor** - Summarizes long outputs, handles file uploads
7. **Queue Manager** - Manages message queue, sequential processing

---

## Technical Details

### Claude Code Integration

- Uses Claude Code's `--print` and `--output-format stream-json` flags for programmatic output
- Session continuity via `--resume` flag with session IDs
- Approval interception by parsing Claude Code's permission prompts
- Image support via Claude Code's multimodal file input

### Session Storage

Sessions stored as JSON files:
```
sessions/
  webapp_session.json
  api_session.json
  scripts_session.json
```

Each contains:
- Session ID for `--resume`
- Conversation history (for display purposes)
- Last activity timestamp

### Dependencies

- `python-telegram-bot` - Telegram API wrapper
- `asyncio` - Async message handling
- Standard library for subprocess management

---

## Running the Bridge

### Starting

```bash
cd C:\_Python\text-claude-code
python bridge.py
```

Or create a batch file `start.bat`:
```batch
@echo off
cd /d C:\_Python\text-claude-code
python bridge.py
```

### Stopping

- `Ctrl+C` in terminal
- Or send `/shutdown` from Telegram (optional feature)

---

## Security Considerations

1. **User ID Verification** - Every message checked against authorized ID before processing
2. **No Sensitive Data in Config** - Bot token should be in environment variable for production
3. **Local Only** - Claude Code runs locally, no code leaves your machine except via Telegram messages
4. **Approval System** - Default "safe" mode requires explicit approval for destructive operations

---

## Example Workflows

### Basic Task

```
You: #webapp Add a dark mode toggle to the settings page

Bot: Working on it...

Bot: ✅ Done! Added dark mode toggle to settings page.
     - Created ThemeToggle component
     - Added theme context provider
     - Updated settings page layout

     📎 full_output.md
```

### With Approval

```
You: #api Delete all test users from the database

Bot: ⚠️ Claude wants to run:
     DELETE FROM users WHERE email LIKE '%@test.com'

     [Allow] [Deny]

You: [Allow]

Bot: ✅ Deleted 47 test users from database.
```

### Error Handling

```
You: #scripts Run the deployment script

Bot: ❌ Error: deployment.py not found in C:\Scripts

     What would you like to do?

You: Check if it's in the deploy subfolder

Bot: Found it at C:\Scripts\deploy\deployment.py. Running now...

Bot: ✅ Deployment complete.
```

### Image Support

```
You: #webapp [attached screenshot of broken UI]
     Fix this alignment issue

Bot: Working on it...

Bot: ✅ Fixed the flexbox alignment in Header.tsx
     📎 full_output.md
```

---

## Future Enhancements (Out of Scope for V1)

- Voice message support (transcribe and send to Claude)
- Multiple user support with permissions
- Web dashboard for monitoring
- Scheduled tasks
- Git integration shortcuts (`/commit`, `/push`)
- Cost tracking and limits

---

## File Structure

```
text-claude-code/
├── bridge.py           # Main entry point
├── config.json         # Configuration file
├── src/
│   ├── __init__.py
│   ├── telegram_bot.py     # Telegram API handling
│   ├── message_router.py   # Parse and route messages
│   ├── session_manager.py  # Session persistence
│   ├── claude_interface.py # Claude Code process management
│   ├── approval_handler.py # Permission request handling
│   ├── output_processor.py # Output formatting and file uploads
│   └── queue_manager.py    # Message queue
├── sessions/           # Session storage directory
├── outputs/            # Temporary output files
└── start.bat           # Quick start script
```

---

## Summary

| Aspect | Decision |
|--------|----------|
| Platform | Telegram |
| Project selection | `#projectname` hashtag syntax |
| Security | Single user ID whitelist |
| Sessions | Persistent with `/new` reset |
| Approvals | Configurable (safe/ask-all/auto-all) |
| Long output | Summary + .md file upload |
| Images | Supported |
| Queue | Sequential, FIFO |
| Notifications | Minimal |
| Errors | Report and wait for guidance |
| Service type | Manual start Python script |
