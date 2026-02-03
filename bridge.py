#!/usr/bin/env python3
"""Claude Code Telegram Bridge - Main Entry Point.

Control Claude Code on your Windows PC via Telegram messages from your phone.
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from src.config import Config
from src.telegram_bot import TelegramBot

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Main entry point."""
    # Load configuration
    config_path = Path(__file__).parent / "config.json"

    if not config_path.exists():
        logger.error(
            f"Configuration file not found: {config_path}\n"
            "Please create config.json based on config.example.json"
        )
        sys.exit(1)

    try:
        config = Config.load(config_path)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    # Validate configuration
    if not config.telegram.bot_token or config.telegram.bot_token == "YOUR_BOT_TOKEN":
        logger.error("Please set your Telegram bot token in config.json")
        sys.exit(1)

    if config.telegram.authorized_user_id == 0:
        logger.error("Please set your Telegram user ID in config.json")
        sys.exit(1)

    if not config.projects:
        logger.warning("No projects configured. Use /addproject to add projects.")

    # Create and start bot
    bot = TelegramBot(config)

    # Handle shutdown gracefully
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()

    # Register signal handlers (Unix-style, may not work on Windows)
    try:
        loop.add_signal_handler(signal.SIGINT, signal_handler)
        loop.add_signal_handler(signal.SIGTERM, signal_handler)
    except NotImplementedError:
        # Windows doesn't support add_signal_handler
        pass

    try:
        logger.info("Starting Claude Code Telegram Bridge...")
        logger.info(f"Authorized user ID: {config.telegram.authorized_user_id}")
        logger.info(f"Projects: {list(config.projects.keys())}")

        await bot.start()
        logger.info("Bot is running. Press Ctrl+C to stop.")

        # Keep running until stop signal
        await stop_event.wait()

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise
    finally:
        logger.info("Stopping bot...")
        await bot.stop()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
