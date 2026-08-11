import tempfile
import unittest
import stat
from pathlib import Path


from codex_opencode_go_bridge.state import SQLiteStateStore


class SQLiteStateStoreTests(unittest.TestCase):
    def test_round_trip_and_call_id_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStateStore(Path(tmp) / "state.sqlite3")
            state = {
                "messages": [{"role": "user", "content": "hello"}],
                "pending_call_ids": ["call_a", "call_b"],
            }
            store.put("resp_1", state)

            self.assertEqual(store.get("resp_1"), state)
            self.assertEqual(store.find_by_call_ids(["call_b"]), state)

    def test_missing_state_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStateStore(Path(tmp) / "state.sqlite3")
            self.assertIsNone(store.get("missing"))
            self.assertIsNone(store.find_by_call_ids(["missing"]))

    def test_state_database_is_private_to_the_current_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            store = SQLiteStateStore(path)
            self.addCleanup(store.close)
            permissions = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(permissions & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
