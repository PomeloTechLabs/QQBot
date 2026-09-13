from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys
from pathlib import Path

_INSTANCE_LOCK: socket.socket | None = None


def _acquire_instance_lock() -> bool:
    global _INSTANCE_LOCK
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lock_socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        lock_socket.bind(("127.0.0.1", 17863))
        lock_socket.listen(1)
    except OSError:
        lock_socket.close()
        return False

    _INSTANCE_LOCK = lock_socket
    return True


def _setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="VintagePomeloBot / 小柚 QQ 机器人")
    parser.add_argument("--config", default="config.toml", metavar="PATH")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    _setup_logging(args.debug)
    logger = logging.getLogger("bot")

    if not _acquire_instance_lock():
        logger.error("Detected another VintagePomeloBot instance already running")
        sys.exit(1)

    from src.bot_core import BotCore
    from src.config import load_config
    from src.web_server import start_web_thread

    config_path = Path(args.config)
    logger.info("Loading config: %s", config_path.resolve())

    try:
        config = load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Failed to load config: %s", exc)
        sys.exit(1)

    logger.info("Bot QQ: %s  nickname: %s", config.bot.qq_id, config.bot.nickname)
    logger.info("NapCat: ws://%s:%s", config.napcat.host, config.napcat.port)
    logger.info("Ollama: %s (%s)", config.ollama.model, config.ollama.base_url)
    logger.info("Support Agent: %s", "Copilot CLI" if config.agent.enabled else "disabled")

    bot = BotCore(config)
    if config.web.enabled:
        start_web_thread(
            config,
            knowledge_store=bot.knowledge_store,
            knowledge_candidates=bot.knowledge_candidates,
            agent_activity=bot.agent_activity,
            port=config.web.port,
        )
    else:
        logger.info("WebUI disabled by config")

    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Received shutdown signal, exiting")


if __name__ == "__main__":
    main()
