"""Read-only Google sources -> private GCS archive -> document context API.

Every run enumerates the entire configured source. Per-revision completion records
make interrupted scans restartable without repeating successful ingestion.
"""
import base64
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryFile

LOG = logging.getLogger(__name__)
MIME_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/tiff": ".tiff",
    "image/bmp": ".bmp", "image/heic": ".heic", "application/pdf": ".pdf",
    "application/msword": ".doc", "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt", "application/rtf": ".rtf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt", "application/epub+zip": ".epub",
    "text/plain": ".txt", "text/csv": ".csv", "text/tab-separated-values": ".tsv",
    "text/html": ".html", "text/markdown": ".md", "application/xml": ".xml",
    "text/xml": ".xml", "message/rfc822": ".eml",
}
GOOGLE_NATIVE = "application/vnd.google-apps."
EXPORTS = {
    GOOGLE_NATIVE + "document": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    GOOGLE_NATIVE + "spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    GOOGLE_NATIVE + "presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    GOOGLE_NATIVE + "drawing": ("application/pdf", ".pdf"),
    GOOGLE_NATIVE + "script": (GOOGLE_NATIVE + "script+json", ".json"),
}
FILE_FIELDS = "id,name,mimeType,version,modifiedTime,size,webViewLink,parents,description,shortcutDetails"


def execute(request):
    return request.execute(num_retries=5)


def items(service, provider, query="", drive_id=""):
    token = None
    while True:
        if provider == "gmail":
            page = execute(service.users().messages().list(
                userId="me", maxResults=500, includeSpamTrash=True, q=query, pageToken=token))
            entries = page.get("messages", [])
        else:
            options = {"corpora": "drive", "driveId": drive_id} if drive_id else {"corpora": "user"}
            page = execute(service.files().list(
                q="trashed = false and mimeType != 'application/vnd.google-apps.folder'" + (f" and ({query})" if query else ""),
                pageSize=1000, pageToken=token, supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                fields=f"nextPageToken,incompleteSearch,files({FILE_FIELDS})",
                **options))
            if page.get("incompleteSearch"):
                raise RuntimeError("Drive search incomplete; configure a separate connection per shared drive")
            entries = page.get("files", [])
        yield from entries
        token = page.get("nextPageToken")
        if not token:
            return


def object_key(provider, item):
    # Gmail message contents are immutable; Drive edits produce a new version.
    revision = item.get("version", "") if provider == "drive" else ""
    if provider == "drive" and not revision:
        raise ValueError("Drive file is missing its version")
    return hashlib.sha256(f"{provider}:{item['id']}:{revision}".encode()).hexdigest()


def download(service, provider, item, output, max_bytes, heartbeat=lambda: None):
    if provider == "gmail":
        message = execute(service.users().messages().get(userId="me", id=item["id"], format="raw"))
        raw = message["raw"]
        if len(raw) > (max_bytes * 4 // 3 + 4):
            raise ValueError("Source exceeds MAX_SOURCE_BYTES")
        output.write(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        metadata = {k: v for k, v in message.items() if k != "raw"}
        return item["id"] + ".eml", "message/rfc822", metadata
    from googleapiclient.http import MediaIoBaseDownload

    native_export = EXPORTS.get(item["mimeType"])
    if not native_export and int(item.get("size", 0)) > max_bytes:
        raise ValueError("Source exceeds MAX_SOURCE_BYTES")
    if native_export:
        mime, suffix = native_export
        request = service.files().export_media(fileId=item["id"], mimeType=mime)
    else:
        mime, suffix = item["mimeType"], MIME_EXTENSIONS.get(item["mimeType"])
        request = service.files().get_media(fileId=item["id"], supportsAllDrives=True)
    if item.get("resourceKey"):
        request.headers["X-Goog-Drive-Resource-Keys"] = f"{item['id']}/{item['resourceKey']}"
    downloader = MediaIoBaseDownload(output, request, chunksize=1024 * 1024)
    done = False
    while not done:
        heartbeat()
        _, done = downloader.next_chunk(num_retries=5)
        if output.tell() > max_bytes:
            raise ValueError("Source exceeds MAX_SOURCE_BYTES")
    # Do not mark changing content with an earlier revision's completion record.
    check = service.files().get(fileId=item["id"], fields="version", supportsAllDrives=True)
    if item.get("resourceKey"):
        check.headers["X-Goog-Drive-Resource-Keys"] = f"{item['id']}/{item['resourceKey']}"
    latest = execute(check)
    if latest.get("version") != item["version"]:
        raise RuntimeError("Drive file changed during download; retry next scan")
    name = Path(item["name"].replace("\\", "/")).name
    if suffix and (native_export or not Path(name).suffix) and Path(name).suffix.lower() != suffix:
        name += suffix
    return name, mime, {**item, "archiveMimeType": mime, "exported": bool(native_export)}


class Archive:
    def __init__(self, bucket):
        self.bucket = bucket

    def read(self, name):
        from google.api_core.exceptions import NotFound
        try:
            return json.loads(self.bucket.blob(name).download_as_text())
        except NotFound:
            return None

    def write(self, name, value):
        self.bucket.blob(name).upload_from_string(json.dumps(value), content_type="application/json")

    def upload(self, key, source, mime):
        source.seek(0)
        self.bucket.blob("raw/" + key).upload_from_file(source, content_type=mime)


class Lease:
    """Generation preconditions exclude overlapping scheduler/manual executions."""
    def __init__(self, bucket):
        self.blob = bucket.blob("state/lease.json")
        self.generation = None

    def acquire(self):
        from google.api_core.exceptions import NotFound, PreconditionFailed
        try:
            self.blob.reload()
            generation = self.blob.generation
            data = json.loads(self.blob.download_as_text(if_generation_match=generation))
            if data["expires"] > time.time():
                return False
        except NotFound:
            generation = 0
        try:
            self.blob.upload_from_string(json.dumps({"expires": time.time() + 1800}),
                                         if_generation_match=generation)
        except PreconditionFailed:
            return False
        self.generation = self.blob.generation
        return True

    def renew(self):
        self.blob.upload_from_string(json.dumps({"expires": time.time() + 1800}),
                                     if_generation_match=self.generation)
        self.generation = self.blob.generation

    def release(self):
        self.blob.delete(if_generation_match=self.generation)


def process(service, provider, item, archive, ingest, max_bytes, heartbeat=lambda: None, visited=()):
    key = object_key(provider, item)
    if item["id"] in visited:
        raise ValueError("Drive shortcut cycle")
    if provider == "drive" and item["mimeType"] == GOOGLE_NATIVE + "shortcut":
        details = item["shortcutDetails"]
        request = service.files().get(fileId=details["targetId"], fields=FILE_FIELDS, supportsAllDrives=True)
        resource_key = details.get("targetResourceKey")
        if resource_key:
            request.headers["X-Goog-Drive-Resource-Keys"] = f"{details['targetId']}/{resource_key}"
        target = execute(request)
        if resource_key:
            target["resourceKey"] = resource_key
        status = process(service, provider, target, archive, ingest, max_bytes, heartbeat, (*visited, item["id"]))
        archive.write("metadata/" + key + ".json", {
            "provider": provider, "source": item, "status": "shortcut",
            "target_object_key": object_key(provider, target)})
        # Resolve again on later scans so target edits are captured.
        return status
    marker = "completed/" + key + ".json"
    previous = archive.read(marker)
    if previous and (ingest is None or previous["status"] in {"ingested", "archive_only", "metadata_only"}):
        return "unchanged"
    if provider == "drive" and item["mimeType"].startswith(GOOGLE_NATIVE) and item["mimeType"] not in EXPORTS:
        record = {"provider": provider, "source": item, "status": "metadata_only",
                  "reason": "No supported content export for this Google-native type", "raw_object": None}
        archive.write("metadata/" + key + ".json", record)
        archive.write(marker, record)
        return "metadata_only"
    with TemporaryFile() as source:
        filename, mime, metadata = download(service, provider, item, source, max_bytes, heartbeat)
        size = source.tell()
        heartbeat()
        archive.upload(key, source, mime)
        record = {"provider": provider, "source": metadata, "filename": filename,
                  "size_bytes": size, "raw_object": "raw/" + key, "status": "archived"}
        # Persist provenance before parsing, including for parser failures.
        archive.write("metadata/" + key + ".json", record)
        if ingest:
            heartbeat()
            source.seek(0)
            record.update(ingest(filename, mime, source, size))
        heartbeat()
        archive.write(marker, record)
    return record["status"]


def make_ingest(url):
    import requests
    from google.auth.transport.requests import Request
    from google.oauth2.id_token import fetch_id_token

    # Query the deployed parser once per job; new formats need no connector release.
    token = fetch_id_token(Request(), url)
    formats = requests.get(url + "/formats", headers={"Authorization": "Bearer " + token}, timeout=(30, 60))
    formats.raise_for_status()
    capabilities = formats.json()
    extensions = set(capabilities["extensions"])
    max_bytes = capabilities["max_bytes"]

    def ingest(filename, mime, source, size):
        if size > max_bytes or Path(filename).suffix.lower() not in extensions:
            return {"status": "archive_only", "reason": "Parser size or format limit"}
        token = fetch_id_token(Request(), url)
        response = requests.post(url + "/documents", headers={"Authorization": "Bearer " + token},
                                 files={"file": (filename, source, mime)}, timeout=(30, 910))
        response.raise_for_status()
        return {"status": "ingested", "document_id": response.json()["document_id"]}
    return ingest


def main():
    import httplib2
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from google_auth_httplib2 import AuthorizedHttp
    from google.cloud import storage

    logging.basicConfig(level=logging.INFO)
    provider = os.environ["CONNECTOR_PROVIDER"]
    if provider not in {"gmail", "drive"}:
        raise ValueError("Unknown connector provider")
    credentials = Credentials.from_authorized_user_info(json.loads(os.environ["GOOGLE_OAUTH_CREDENTIALS"]))
    service = build(provider, "v1" if provider == "gmail" else "v3",
                    http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=120)), cache_discovery=False)
    bucket = storage.Client().bucket(os.environ["CONNECTOR_BUCKET"])
    lease = Lease(bucket)
    if not lease.acquire():
        LOG.info("Another execution holds the connection lease")
        return
    counts = {}
    try:
        url = os.environ.get("INGESTION_URL", "").rstrip("/")
        ingest = make_ingest(url) if url else None
        for item in items(service, provider, os.environ.get("SOURCE_QUERY", ""), os.environ.get("DRIVE_ID", "")):
            lease.renew()
            try:
                status = process(service, provider, item, Archive(bucket), ingest,
                                 int(os.environ.get("MAX_SOURCE_BYTES", str(256 * 1024 * 1024))), lease.renew)
            except RefreshError:
                raise
            except Exception as error:
                # Do not log source names, content, OAuth responses or tokens.
                LOG.error("Item failed key=%s error_type=%s", object_key(provider, item), type(error).__name__)
                if isinstance(error, HttpError) and error.resp.status == 401:
                    raise
                status = "failed"
            counts[status] = counts.get(status, 0) + 1
        Archive(bucket).write("state/last_run.json", {"finished_at": time.time(), "counts": counts})
        LOG.info("Scan finished counts=%s", counts)
        if counts.get("failed"):
            raise RuntimeError("Scan has failed items; rerun to retry them")
    finally:
        lease.release()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        LOG.error("Connector execution failed error_type=%s", type(error).__name__)
        sys.exit(1)
