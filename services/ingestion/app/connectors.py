"""Read-only Gmail and Drive connectors. Credentials stay on the server."""
import base64
import os
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

MAX_BYTES = 20 * 1024 * 1024
EXPORTS = {
    "application/vnd.google-apps.document": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
}


@dataclass
class Item:
    provider: str
    account: str
    external_id: str
    filename: str
    content: bytes
    revision: str = ""
    kind: str = "file"
    metadata: dict = field(default_factory=dict)


class GoogleConnector:
    def __init__(self, client=None):
        self.client = client or httpx.Client(timeout=90)
        self.owns_client = client is None
        self.token = None

    def close(self):
        if self.owns_client:
            self.client.close()

    def access_token(self):
        if self.token:
            return self.token
        if os.environ.get("GOOGLE_REFRESH_TOKEN"):
            response = self.client.post("https://oauth2.googleapis.com/token", data={
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"], "grant_type": "refresh_token"})
            response.raise_for_status()
            self.token = response.json()["access_token"]
        else:
            self.token = os.environ["GOOGLE_ACCESS_TOKEN"]
        return self.token

    def get(self, url, params=None, binary=False):
        # All callers construct URLs from fixed Google hosts and quoted IDs.
        for attempt in range(2):
            with self.client.stream("GET", url, params=params,
                                    headers={"Authorization": "Bearer " + self.access_token()}) as response:
                if response.status_code == 401 and attempt == 0 and os.environ.get("GOOGLE_REFRESH_TOKEN"):
                    self.token = None
                    continue
                response.raise_for_status()
                content = bytearray()
                ceiling = MAX_BYTES if binary else MAX_BYTES * 2
                for part in response.iter_bytes():
                    content.extend(part)
                    if len(content) > ceiling:
                        raise OverflowError("Connector response exceeds size limit")
                if binary:
                    return bytes(content)
                import json
                return json.loads(content)
        raise RuntimeError("Google authorization failed")

    def page(self, provider, query, page_token=None, page_size=10):
        if not query.strip():
            raise ValueError("An explicit connector query is required")
        if provider == "gmail":
            base = "https://gmail.googleapis.com/gmail/v1/users/me"
            account = self.get(base + "/profile")["emailAddress"].casefold()
            params = {"q": query, "maxResults": page_size}
            if page_token:
                params["pageToken"] = page_token
            page = self.get(base + "/messages", params)
            items = []
            for message in page.get("messages", []):
                raw = self.get(base + "/messages/" + quote(message["id"], safe=""), {"format": "raw"})
                content = base64.urlsafe_b64decode(raw["raw"] + "=" * (-len(raw["raw"]) % 4))
                if len(content) > MAX_BYTES:
                    raise OverflowError("Gmail message exceeds 20 MiB")
                items.append(Item("gmail", account, raw["id"], raw["id"] + ".eml", content,
                                  kind="email", metadata={k: raw[k] for k in ("threadId", "internalDate", "labelIds") if k in raw}))
            return items, page.get("nextPageToken")
        if provider != "drive":
            raise ValueError("Unknown connector")
        base = "https://www.googleapis.com/drive/v3"
        account = self.get(base + "/about", {"fields": "user(permissionId,emailAddress)"})["user"]["permissionId"]
        params = {"q": "trashed = false and (" + query + ")", "pageSize": page_size,
                  "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,version,modifiedTime,size,parents,webViewLink)",
                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if page_token:
            params["pageToken"] = page_token
        page = self.get(base + "/files", params)
        if page.get("incompleteSearch"):
            raise ValueError("Drive search is incomplete; narrow the query to a specific drive")
        items = []
        for file in page.get("files", []):
            mime = file["mimeType"]
            if mime in {"application/vnd.google-apps.folder", "application/vnd.google-apps.shortcut"}:
                continue
            if int(file.get("size", 0)) > MAX_BYTES:
                raise OverflowError("Drive file exceeds 20 MiB: " + file["id"])
            url, filename = base + "/files/" + quote(file["id"], safe=""), file["name"]
            if mime in EXPORTS:
                export_mime, extension = EXPORTS[mime]
                content = self.get(url + "/export", {"mimeType": export_mime}, binary=True)
                filename += extension
            else:
                content = self.get(url, {"alt": "media", "supportsAllDrives": "true"}, binary=True)
            items.append(Item("drive", account, file["id"], filename, content,
                              str(file.get("version", file.get("modifiedTime", ""))), metadata=file))
        return items, page.get("nextPageToken")
