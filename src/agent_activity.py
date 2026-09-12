"""Ephemeral, in-memory activity events for the web console.

This is intentionally a live observation channel, not an audit log.  It owns
no file path and never replays completed work to a later browser connection.
"""

from __future__ import annotations

from datetime import datetime
from queue import Empty, Full, Queue
from threading import Lock
from typing import Any
from uuid import uuid4


class AgentActivityStream:
    """Fan out transient Agent status updates to currently connected browsers."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._subscribers: dict[str, Queue[dict[str, Any]]] = {}
        self._active: dict[str, dict[str, Any]] = {}

    def start(
        self,
        operation: str,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> str:
        activity_id = uuid4().hex
        event = self._event(
            activity_id,
            operation,
            "started",
            message,
            detail,
        )
        with self._lock:
            self._active[activity_id] = event
            self._broadcast_locked(event)
        return activity_id

    def progress(
        self,
        activity_id: str | None,
        operation: str,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if not activity_id:
            return
        event = self._event(activity_id, operation, "progress", message, detail)
        with self._lock:
            if activity_id not in self._active:
                return
            self._active[activity_id] = event
            self._broadcast_locked(event)

    def finish(
        self,
        activity_id: str | None,
        operation: str,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
        failed: bool = False,
    ) -> None:
        if not activity_id:
            return
        event = self._event(
            activity_id,
            operation,
            "failed" if failed else "completed",
            message,
            detail,
        )
        with self._lock:
            if activity_id not in self._active:
                return
            self._active.pop(activity_id, None)
            self._broadcast_locked(event)

    def subscribe(self) -> tuple[str, Queue[dict[str, Any]], list[dict[str, Any]]]:
        """Register one browser and return only currently active work.

        Completed events are not retained, so a newly opened tab can never read
        historical messages from this object.
        """
        subscriber_id = uuid4().hex
        event_queue: Queue[dict[str, Any]] = Queue(maxsize=100)
        with self._lock:
            self._subscribers[subscriber_id] = event_queue
            active = [dict(event, phase="active") for event in self._active.values()]
        return subscriber_id, event_queue, active

    def unsubscribe(self, subscriber_id: str) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    @staticmethod
    def next_event(event_queue: Queue[dict[str, Any]], timeout: float) -> dict[str, Any] | None:
        try:
            return event_queue.get(timeout=timeout)
        except Empty:
            return None

    @staticmethod
    def _event(
        activity_id: str,
        operation: str,
        phase: str,
        message: str,
        detail: dict[str, Any] | None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "id": activity_id,
            "operation": operation,
            "phase": phase,
            "message": message,
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        if detail:
            event["detail"] = detail
        return event

    def _broadcast(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._broadcast_locked(event)

    def _broadcast_locked(self, event: dict[str, Any]) -> None:
        """Deliver under ``_lock`` so subscriptions cannot race snapshots."""
        for event_queue in self._subscribers.values():
            try:
                event_queue.put_nowait(event)
            except Full:
                # A slow or backgrounded tab must not retain Agent activity in
                # memory. Drop its oldest event and keep the freshest state.
                try:
                    event_queue.get_nowait()
                    event_queue.put_nowait(event)
                except (Empty, Full):
                    pass
