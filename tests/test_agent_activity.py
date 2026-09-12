from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent_activity import AgentActivityStream


class AgentActivityStreamTests(unittest.TestCase):
    def test_events_are_live_only_and_completed_work_is_not_replayed(self) -> None:
        stream = AgentActivityStream()
        subscriber_id, event_queue, active = stream.subscribe()
        self.assertEqual(active, [])

        activity_id = stream.start("技术支持答复", "正在处理问题。")
        started = stream.next_event(event_queue, timeout=0.1)
        self.assertEqual(started["phase"], "started")
        self.assertEqual(started["id"], activity_id)

        stream.finish(activity_id, "技术支持答复", "Agent 已完成。")
        completed = stream.next_event(event_queue, timeout=0.1)
        self.assertEqual(completed["phase"], "completed")
        stream.unsubscribe(subscriber_id)

        later_id, later_queue, later_active = stream.subscribe()
        self.assertNotEqual(subscriber_id, later_id)
        self.assertEqual(later_active, [])
        self.assertIsNone(stream.next_event(later_queue, timeout=0.01))
        stream.unsubscribe(later_id)
