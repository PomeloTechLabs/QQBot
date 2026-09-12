from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from typing import Any, Awaitable, Callable

import websockets
from websockets.exceptions import WebSocketException

from .config import NapCatConfig

logger = logging.getLogger("napcat_client")


class NapCatClient:
    """Maintain the long-lived WebSocket connection to NapCat."""

    def __init__(self, config: NapCatConfig) -> None:
        self._config = config
        self._ws: Any = None
        self._echo_futures: dict[str, asyncio.Future] = {}
        self._event_tasks: set[asyncio.Task] = set()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _ws_url(self) -> str:
        return f"ws://{self._config.host}:{self._config.port}"

    def _headers(self) -> dict[str, str]:
        if self._config.access_token:
            return {"Authorization": f"Bearer {self._config.access_token}"}
        return {}

    def _connect_options(self) -> dict[str, Any]:
        """Bridge the header rename between websockets 12 and 14+.

        NapCat speaks the same protocol in both cases.  ``websockets`` renamed
        ``extra_headers`` to ``additional_headers`` in its modern asyncio
        client, so pinning the old spelling prevents the bot from receiving
        any QQ event on newer installations.
        """
        header_option = (
            "additional_headers"
            if "additional_headers" in inspect.signature(websockets.connect).parameters
            else "extra_headers"
        )
        return {
            header_option: self._headers(),
            "ping_interval": 20,
            "ping_timeout": 20,
        }

    async def _send_action(self, action: str, params: dict) -> dict | None:
        """Send an action to NapCat and wait for its echo response."""
        if not self._ws:
            logger.warning("Action %r failed to send because NapCat is not connected", action)
            return None

        echo = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._echo_futures[echo] = fut
        payload = {"action": action, "params": params, "echo": echo}

        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(fut, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning(
                "Action %r 响应超时，通常是 NapCat 没有及时返回 echo，或接收循环被阻塞",
                action,
            )
            return None
        except Exception as exc:
            logger.error("Action %r failed: %s", action, exc)
            return None
        finally:
            self._echo_futures.pop(echo, None)

    async def send_group_msg(self, group_id: int | str, message: list[dict]) -> None:
        await self._send_action(
            "send_group_msg",
            {"group_id": int(group_id), "message": message},
        )

    async def send_private_msg(self, user_id: int | str, message: list[dict]) -> None:
        await self._send_action(
            "send_private_msg",
            {"user_id": int(user_id), "message": message},
        )

    async def _safe_on_event(
        self,
        on_event: Callable[[dict], Awaitable[None]],
        data: dict,
    ) -> None:
        try:
            await on_event(data)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("on_event callback failed")

    def _start_event_task(
        self,
        on_event: Callable[[dict], Awaitable[None]],
        data: dict,
    ) -> None:
        task = asyncio.create_task(self._safe_on_event(on_event, data))
        self._event_tasks.add(task)
        task.add_done_callback(self._event_tasks.discard)

    async def _cancel_event_tasks(self) -> None:
        if not self._event_tasks:
            return

        tasks = list(self._event_tasks)
        self._event_tasks.clear()
        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self, on_event: Callable[[dict], Awaitable[None]]) -> None:
        """
        Keep the connection alive, continuously receive NapCat events,
        and reconnect with backoff when disconnected.
        """
        delay = self._config.reconnect_interval
        url = self._ws_url()

        while True:
            try:
                logger.info("Connecting to NapCat: %s", url)
                async with websockets.connect(url, **self._connect_options()) as ws:
                    self._ws = ws
                    self._connected = True
                    delay = self._config.reconnect_interval
                    logger.info("NapCat connected")

                    async for raw_msg in ws:
                        if not isinstance(raw_msg, str):
                            continue
                        try:
                            data: dict = json.loads(raw_msg)
                        except json.JSONDecodeError:
                            continue

                        if "echo" in data and "post_type" not in data:
                            echo = data.get("echo")
                            if echo and echo in self._echo_futures:
                                fut = self._echo_futures[echo]
                                if not fut.done():
                                    fut.set_result(data)
                            continue

                        # Keep reading the socket while business logic runs in parallel.
                        self._start_event_task(on_event, data)

            except asyncio.CancelledError:
                raise
            except (WebSocketException, OSError, ConnectionResetError) as exc:
                logger.warning("NapCat disconnected: %s", exc)
            except Exception:
                logger.exception("Unexpected NapCat connection error")
            finally:
                self._ws = None
                self._connected = False
                for fut in self._echo_futures.values():
                    if not fut.done():
                        fut.cancel()
                self._echo_futures.clear()
                await self._cancel_event_tasks()

            logger.info("Reconnecting after %ss...", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
