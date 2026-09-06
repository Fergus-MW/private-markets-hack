import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import store as store_module
from app.main import app
from app.store import MAX_BLOB_BYTES, RPC_BODY_LIMIT, SourceTooLarge, Store


class BlobLimitTests(unittest.TestCase):
    def test_limit_leaves_room_for_base64_and_the_json_envelope(self):
        # base64 inflates by 4/3, so the stored form must still fit the RPC body.
        self.assertLess(MAX_BLOB_BYTES * 4 // 3, RPC_BODY_LIMIT)

    def test_oversized_source_is_refused_before_the_database_call(self):
        store = Store.__new__(Store)
        with patch.object(Store, "query", side_effect=AssertionError("must not reach the database")):
            with self.assertRaises(SourceTooLarge):
                store.save_source_bytes("k", b"x" * (MAX_BLOB_BYTES + 1))

    def test_a_source_at_the_limit_is_still_written(self):
        store = Store.__new__(Store)
        with patch.object(Store, "query", return_value=[{"result": []}]) as query:
            store.save_source_bytes("k", b"x" * MAX_BLOB_BYTES)
        query.assert_called_once()

    def test_the_limit_follows_the_configured_database_setting(self):
        with patch.dict("os.environ", {"SURREAL_HTTP_MAX_RPC_BODY_SIZE": str(64 << 20)}):
            import importlib
            reloaded = importlib.reload(store_module)
            self.assertGreater(reloaded.MAX_BLOB_BYTES, MAX_BLOB_BYTES)
        importlib.reload(store_module)


class AdvertisedLimitTests(unittest.TestCase):
    def test_formats_never_advertises_more_than_can_be_stored(self):
        # Connectors skip anything larger as archive-only, so this number must be
        # honest or one big file fails an entire scan on an unsurvivable write.
        body = TestClient(app).get("/formats").json()
        self.assertLessEqual(body["max_bytes"], MAX_BLOB_BYTES)


if __name__ == "__main__":
    unittest.main()
