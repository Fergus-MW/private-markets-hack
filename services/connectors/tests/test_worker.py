import base64
import io
import unittest
from unittest.mock import MagicMock, patch

from app.worker import (EXPORTS, GOOGLE_NATIVE, Archive, Lease, credentials_info, download, items,
                        make_ingest, object_prefix, object_key, process)


class MemoryArchive:
    def __init__(self):
        self.records = {}
        self.raw = {}

    def read(self, key):
        return self.records.get(key)

    def write(self, key, value):
        self.records[key] = dict(value)

    def upload(self, key, source, mime):
        source.seek(0)
        self.raw[key] = source.read()


class WorkerTests(unittest.TestCase):
    def test_gmail_pages_include_every_folder_and_empty_pages(self):
        service = MagicMock()
        request = service.users().messages().list
        request.return_value.execute.side_effect = [
            {"messages": [{"id": "a"}], "nextPageToken": "second"},
            {"nextPageToken": "third"}, {"messages": [{"id": "b"}]}]
        self.assertEqual([i["id"] for i in items(service, "gmail")], ["a", "b"])
        self.assertEqual(request.call_args_list[1].kwargs["pageToken"], "second")
        self.assertEqual(request.call_args_list[2].kwargs["pageToken"], "third")
        self.assertTrue(request.call_args.kwargs["includeSpamTrash"])
        self.assertEqual(request.call_args.kwargs["q"], "")

    def test_drive_shared_drive_and_filter(self):
        service = MagicMock()
        request = service.files().list
        request.return_value.execute.side_effect = [
            {"files": [{"id": "one"}], "nextPageToken": "next"}, {"files": [{"id": "two"}]}]
        self.assertEqual(len(list(items(service, "drive", "'folder' in parents", "shared"))), 2)
        self.assertEqual(request.call_args.kwargs["driveId"], "shared")
        self.assertEqual(request.call_args.kwargs["corpora"], "drive")
        self.assertNotIn("image/", request.call_args.kwargs["q"])
        self.assertIn("mimeType != 'application/vnd.google-apps.folder'", request.call_args.kwargs["q"])
        self.assertIn("'folder' in parents", request.call_args.kwargs["q"])

    def test_incomplete_drive_scan_is_not_success(self):
        service = MagicMock()
        service.files().list().execute.return_value = {"incompleteSearch": True}
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            list(items(service, "drive"))

    def test_drive_revision_changes_key(self):
        self.assertNotEqual(object_key("drive", {"id": "a", "version": "1"}),
                            object_key("drive", {"id": "a", "version": "2"}))
        with self.assertRaises(ValueError):
            object_key("drive", {"id": "a"})

    def gmail(self):
        service = MagicMock()
        self.email = b"Subject: example\r\n\r\nbody"
        service.users().messages().get().execute.return_value = {
            "id": "a", "threadId": "thread", "raw": base64.urlsafe_b64encode(self.email).decode().rstrip("=")}
        return service

    def test_archive_and_ingest_are_idempotent(self):
        service, archive = self.gmail(), MemoryArchive()
        ingest = MagicMock(return_value={"status": "ingested", "document_id": "doc"})
        self.assertEqual(process(service, "gmail", {"id": "a"}, archive, ingest, 1000), "ingested")
        self.assertEqual(process(service, "gmail", {"id": "a"}, archive, ingest, 1000), "unchanged")
        ingest.assert_called_once()
        self.assertEqual(next(iter(archive.raw.values())), self.email)
        record = archive.read("completed/" + object_key("gmail", {"id": "a"}) + ".json")
        self.assertNotIn("raw", record["source"])
        self.assertEqual(record["source"]["threadId"], "thread")

    def test_failed_ingestion_retries_without_false_completion(self):
        service, archive = self.gmail(), MemoryArchive()
        ingest = MagicMock(side_effect=RuntimeError("unavailable"))
        with self.assertRaises(RuntimeError):
            process(service, "gmail", {"id": "a"}, archive, ingest, 1000)
        self.assertTrue(archive.raw)
        self.assertFalse(any(k.startswith("completed/") for k in archive.records))
        ingest.side_effect = None
        ingest.return_value = {"status": "ingested", "document_id": "doc"}
        self.assertEqual(process(service, "gmail", {"id": "a"}, archive, ingest, 1000), "ingested")

    def test_archive_only_can_later_enable_ingestion(self):
        service, archive = self.gmail(), MemoryArchive()
        self.assertEqual(process(service, "gmail", {"id": "a"}, archive, None, 1000), "archived")
        ingest = MagicMock(return_value={"status": "ingested", "document_id": "doc"})
        self.assertEqual(process(service, "gmail", {"id": "a"}, archive, ingest, 1000), "ingested")

    def test_oversize_source_is_not_completed(self):
        archive = MemoryArchive()
        with self.assertRaises(ValueError):
            process(self.gmail(), "gmail", {"id": "a"}, archive, None, 1)
        self.assertFalse(archive.records)

    def test_parser_limits_archive_without_calling_endpoint(self):
        with patch("requests.get") as get, patch("google.oauth2.id_token.fetch_id_token"), patch("requests.post") as post:
            get.return_value.json.return_value = {"extensions": [".jpg"], "max_bytes": 20 * 1024 * 1024}
            ingest = make_ingest("https://example.run.app")
            self.assertEqual(ingest("x.jpg", "image/jpeg", io.BytesIO(), 21 * 1024 * 1024)["status"], "archive_only")
            self.assertEqual(ingest("x.svg", "image/svg+xml", io.BytesIO(), 100)["status"], "archive_only")
            post.assert_not_called()

    def test_office_pdf_and_text_use_advertised_parser_formats(self):
        with patch("requests.get") as get, patch("google.oauth2.id_token.fetch_id_token", return_value="identity") as token, patch("requests.post") as post:
            extensions = [".doc", ".docx", ".xls", ".xlsx", ".pptx", ".pdf", ".csv", ".md", ".msg"]
            get.return_value.json.return_value = {"extensions": extensions, "max_bytes": 1000}
            post.return_value.json.return_value = {"document_id": "parsed"}
            ingest = make_ingest("https://example.run.app")
            for extension in extensions:
                with self.subTest(extension=extension):
                    result = ingest("report" + extension, "application/octet-stream", io.BytesIO(b"data"), 4)
                    self.assertEqual(result, {"status": "ingested", "document_id": "parsed"})
            get.assert_called_once()
            self.assertEqual(token.call_args.args[1], "https://example.run.app")
            self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer identity")

    def test_google_native_exports_preserve_format_and_provenance(self):
        for native, (mime, extension) in EXPORTS.items():
            with self.subTest(native=native), patch("googleapiclient.http.MediaIoBaseDownload") as downloader:
                service = MagicMock()
                service.files().get().execute.return_value = {"version": "1"}
                downloader.return_value.next_chunk.return_value = (None, True)
                item = {"id": "a", "version": "1", "name": "Engagement", "mimeType": native}
                name, result_mime, metadata = download(service, "drive", item, io.BytesIO(), 1000)
                service.files().export_media.assert_called_once_with(fileId="a", mimeType=mime)
                service.files().get_media.assert_not_called()
                self.assertEqual(name, "Engagement" + extension)
                self.assertEqual(result_mime, mime)
                self.assertEqual(metadata["mimeType"], native)
                self.assertTrue(metadata["exported"])

    def test_binary_files_download_original_bytes_and_preserve_names(self):
        for name, mime, expected in [("Budget.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Budget.xlsx"),
                                     ("Notes.md", "text/plain", "Notes.md"), ("Contract", "application/pdf", "Contract.pdf"),
                                     ("Backup.zip", "application/zip", "Backup.zip")]:
            with self.subTest(name=name), patch("googleapiclient.http.MediaIoBaseDownload") as downloader:
                service = MagicMock()
                service.files().get().execute.return_value = {"version": "1"}
                def write_chunk(**kwargs):
                    downloader.call_args.args[0].write(b"original bytes")
                    return None, True
                downloader.return_value.next_chunk.side_effect = write_chunk
                output = io.BytesIO()
                result = download(service, "drive", {"id": "a", "version": "1", "name": name, "mimeType": mime}, output, 1000)
                self.assertEqual(result[0], expected)
                self.assertEqual(output.getvalue(), b"original bytes")
                service.files().get_media.assert_called_once_with(fileId="a", supportsAllDrives=True)
                service.files().export_media.assert_not_called()

    def test_unexportable_native_file_has_explicit_metadata_only_record(self):
        item = {"id": "a", "version": "1", "name": "Intake form", "mimeType": GOOGLE_NATIVE + "form"}
        archive, service, ingest = MemoryArchive(), MagicMock(), MagicMock()
        self.assertEqual(process(service, "drive", item, archive, ingest, 1000), "metadata_only")
        record = archive.read("completed/" + object_key("drive", item) + ".json")
        self.assertIsNone(record["raw_object"])
        self.assertFalse(archive.raw)
        ingest.assert_not_called()
        service.files.assert_not_called()

    def test_shortcut_resolves_target_and_uses_target_revision(self):
        service, archive = MagicMock(), MemoryArchive()
        target = {"id": "target", "version": "2", "mimeType": GOOGLE_NATIVE + "form", "name": "Form"}
        service.files().get().execute.return_value = target
        shortcut = {"id": "link", "version": "1", "mimeType": GOOGLE_NATIVE + "shortcut",
                    "shortcutDetails": {"targetId": "target", "targetResourceKey": "resource-key"}}
        self.assertEqual(process(service, "drive", shortcut, archive, None, 1000), "metadata_only")
        record = archive.read("metadata/" + object_key("drive", shortcut) + ".json")
        self.assertEqual(record["target_object_key"], object_key("drive", target))
        self.assertIn("completed/" + object_key("drive", target) + ".json", archive.records)

    def test_export_failure_keeps_item_retryable(self):
        archive, service = MemoryArchive(), MagicMock()
        item = {"id": "a", "version": "1", "name": "Large sheet", "mimeType": GOOGLE_NATIVE + "spreadsheet"}
        with patch("googleapiclient.http.MediaIoBaseDownload") as downloader:
            downloader.return_value.next_chunk.side_effect = RuntimeError("export failed")
            with self.assertRaises(RuntimeError):
                process(service, "drive", item, archive, None, 1000)
        self.assertFalse(archive.records)

    def test_changing_drive_download_is_retried(self):
        service = MagicMock()
        service.files().get().execute.return_value = {"version": "2"}
        with patch("googleapiclient.http.MediaIoBaseDownload") as downloader:
            downloader.return_value.next_chunk.return_value = (None, True)
            with self.assertRaisesRegex(RuntimeError, "changed"):
                download(service, "drive", {"id": "a", "version": "1", "name": "x", "mimeType": "image/png"},
                         io.BytesIO(), 1000)

    def test_active_lease_blocks_overlapping_job(self):
        bucket = MagicMock()
        bucket.blob().download_as_text.return_value = '{"expires": 200}'
        with patch("app.worker.time.time", return_value=100):
            self.assertFalse(Lease(bucket).acquire())
        bucket.blob().upload_from_string.assert_not_called()

    def test_expired_lease_uses_generation_precondition(self):
        bucket = MagicMock()
        blob = bucket.blob()
        blob.generation = 7
        blob.download_as_text.return_value = '{"expires": 0}'
        self.assertTrue(Lease(bucket).acquire())
        self.assertEqual(blob.upload_from_string.call_args.kwargs["if_generation_match"], 7)

    def test_lease_race_has_only_one_winner(self):
        from google.api_core.exceptions import NotFound, PreconditionFailed
        bucket = MagicMock()
        bucket.blob().reload.side_effect = NotFound("absent")
        bucket.blob().upload_from_string.side_effect = PreconditionFailed("other worker won")
        self.assertFalse(Lease(bucket).acquire())


if __name__ == "__main__":
    unittest.main()


class MultiAccountTests(unittest.TestCase):
    """Per-account isolation: one bucket and one job serve many connected users."""

    def test_prefix_isolates_objects_and_lease(self):
        bucket = MagicMock()
        Archive(bucket, "u_abc/").write("completed/k.json", {"status": "ingested"})
        bucket.blob.assert_called_with("u_abc/completed/k.json")
        Archive(bucket, "u_abc/").upload("k", io.BytesIO(b"x"), "text/plain")
        bucket.blob.assert_called_with("u_abc/raw/k")
        Lease(bucket, "u_abc/")
        bucket.blob.assert_called_with("u_abc/state/lease.json")

    def test_two_accounts_never_share_a_completion_marker(self):
        bucket = MagicMock()
        blobs = {}
        bucket.blob.side_effect = lambda name: blobs.setdefault(name, MagicMock())
        Archive(bucket, "u_one/").write("completed/same.json", {"status": "ingested"})
        Archive(bucket, "u_two/").write("completed/same.json", {"status": "ingested"})
        self.assertEqual(sorted(blobs), ["u_one/completed/same.json", "u_two/completed/same.json"])

    def test_empty_prefix_preserves_original_layout(self):
        bucket = MagicMock()
        Archive(bucket).write("state/last_run.json", {})
        bucket.blob.assert_called_with("state/last_run.json")
        self.assertEqual(object_prefix({}), "")
        self.assertEqual(object_prefix({"CONNECTOR_PREFIX": ""}), "")

    def test_prefix_is_normalised_and_validated(self):
        self.assertEqual(object_prefix({"CONNECTOR_PREFIX": "/u_abc/"}), "u_abc/")
        self.assertEqual(object_prefix({"CONNECTOR_PREFIX": "u_abc"}), "u_abc/")
        # A traversal or wildcard prefix must not reach another account's keys.
        for bad in ("../other", "u/../..", "a b", "u*"):
            with self.assertRaises(ValueError):
                object_prefix({"CONNECTOR_PREFIX": bad})

    def test_mounted_credentials_used_when_no_secret_named(self):
        self.assertEqual(credentials_info({"GOOGLE_OAUTH_CREDENTIALS": '{"type": "authorized_user"}'}),
                         {"type": "authorized_user"})

    def test_named_secret_overrides_mounted_value(self):
        name = "projects/p/secrets/connector-u-abc-oauth/versions/latest"
        client = MagicMock()
        client.access_secret_version.return_value.payload.data = b'{"refresh_token": "from-secret"}'
        module = MagicMock()
        module.SecretManagerServiceClient.return_value = client
        with patch.dict("sys.modules", {"google.cloud.secretmanager": module}):
            result = credentials_info({"CONNECTOR_SECRET": name,
                                       "GOOGLE_OAUTH_CREDENTIALS": '{"refresh_token": "mounted"}'})
        self.assertEqual(result, {"refresh_token": "from-secret"})
        self.assertEqual(client.access_secret_version.call_args.kwargs["name"], name)
