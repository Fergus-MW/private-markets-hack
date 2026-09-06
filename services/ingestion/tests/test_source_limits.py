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
        # Computed, not reloaded: reloading the module swaps SourceTooLarge for a
        # new class and breaks every other test's isinstance check.
        self.assertEqual(store_module.MAX_BLOB_BYTES, store_module.blob_limit(RPC_BODY_LIMIT))
        self.assertGreater(store_module.blob_limit(64 << 20), store_module.blob_limit(4 << 20))
        self.assertEqual(store_module.blob_limit(1024), 0)


class DatabaseRefusalTests(unittest.TestCase):
    """The configured limit can lag what the database enforces while a change rolls
    out. A 413 from the database must degrade the source, never fail the scan."""

    def refuse(self, status):
        import httpx
        request = httpx.Request("POST", "http://db/rpc")
        return httpx.HTTPStatusError("", request=request,
                                     response=httpx.Response(status, request=request))

    def test_a_database_413_becomes_a_source_too_large(self):
        store = Store.__new__(Store)
        with patch.object(Store, "query", side_effect=self.refuse(413)):
            with self.assertRaises(SourceTooLarge):
                store.save_source_bytes("k", b"x" * 100)

    def test_other_database_errors_still_propagate(self):
        store = Store.__new__(Store)
        with patch.object(Store, "query", side_effect=self.refuse(503)):
            with self.assertRaises(Exception) as caught:
                store.save_source_bytes("k", b"x" * 100)
        self.assertNotIsInstance(caught.exception, SourceTooLarge)


class AdvertisedLimitTests(unittest.TestCase):
    def test_formats_never_advertises_more_than_can_be_stored(self):
        # Connectors skip anything larger as archive-only, so this number must be
        # honest or one big file fails an entire scan on an unsurvivable write.
        body = TestClient(app).get("/formats").json()
        self.assertLessEqual(body["max_bytes"], MAX_BLOB_BYTES)


if __name__ == "__main__":
    unittest.main()
