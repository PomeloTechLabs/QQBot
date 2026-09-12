from __future__ import annotations

import time
import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import CacheConfig
from src.context_cache import ContextCache


class ContextCacheTests(unittest.TestCase):
    def test_user_and_assistant_turns_are_kept_in_order(self) -> None:
        cache = ContextCache(CacheConfig(window_size=3, ttl_seconds=60))
        cache.add_user_turn("g1", "u1", "你好")
        cache.add_assistant_turn("g1", "u1", "你好呀")
        cache.add_user_turn("g1", "u1", "问个问题")

        history = cache.get_history("g1", "u1")
        self.assertEqual(
            history,
            [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好呀"},
                {"role": "user", "content": "问个问题"},
            ],
        )

    def test_limit_and_expiration(self) -> None:
        cache = ContextCache(CacheConfig(window_size=3, ttl_seconds=0))
        cache.add_user_turn("g1", "u1", "a")
        time.sleep(0.01)
        self.assertEqual(cache.get_history("g1", "u1"), [])

        cache = ContextCache(CacheConfig(window_size=3, ttl_seconds=60))
        for index in range(4):
            cache.add_user_turn("g1", "u1", f"u{index}")
            cache.add_assistant_turn("g1", "u1", f"a{index}")

        limited = cache.get_history("g1", "u1", limit=3)
        self.assertEqual(len(limited), 3)
        self.assertEqual(limited[-1]["content"], "a3")


if __name__ == "__main__":
    unittest.main()
