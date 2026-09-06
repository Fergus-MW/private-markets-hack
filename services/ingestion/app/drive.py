"""Deliver first-run drafts to the requesting account's own Google Drive.

Uses the per-account connector credential the frontend stored at sign-in, so a
draft lands in the Drive of the person who asked for it and nobody else's. The
`drive.file` scope only ever grants access to files this application created; it
cannot read or change anything already in the account.
"""
import base64
import json
import os
import re
import uuid

import google.auth
import httpx
from google.auth.transport.requests import Request as AuthRequest

SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER = "Private markets drafts"
FOLDER_MIME = "application/vnd.google-apps.folder"


class DeliveryError(RuntimeError):
    """Delivery failed. Never fatal: the draft is already durable in the project."""


def account_token(tenant):
    if not re.fullmatch(r"u-[a-f0-9]{16}", tenant or ""):
        raise DeliveryError("Drive delivery needs a per-account connector identity")
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise DeliveryError("Drive delivery is not configured for this deployment")
    service, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    service.refresh(AuthRequest())
    name = f"projects/{project}/secrets/connector-{tenant}-oauth/versions/latest"
    response = httpx.get(f"https://secretmanager.googleapis.com/v1/{name}:access",
                         headers={"Authorization": "Bearer " + service.token}, timeout=30)
    if response.status_code != 200:
        raise DeliveryError("No stored Google credential for this account; reconnect Google to enable Drive delivery")
    info = json.loads(base64.b64decode(response.json()["payload"]["data"]))
    if SCOPE not in (info.get("scopes") or []):
        raise DeliveryError("Reconnect Google to grant Drive file access; this account's stored permission is read-only")
    from google.oauth2.credentials import Credentials
    account = Credentials.from_authorized_user_info(info, scopes=[SCOPE])
    account.refresh(AuthRequest())
    return account.token


def call(token, method, url, **kwargs):
    headers = {"Authorization": "Bearer " + token, **kwargs.pop("headers", {})}
    response = httpx.request(method, url, headers=headers, timeout=120, **kwargs)
    if response.status_code >= 400:
        # Never surface Google's response body; it can name unrelated account content.
        raise DeliveryError(f"Google Drive rejected the delivery (HTTP {response.status_code})")
    return response.json()


def folder(token):
    # Under drive.file this search only ever sees folders this application created.
    found = call(token, "GET", "https://www.googleapis.com/drive/v3/files",
                 params={"q": f"name = '{FOLDER}' and mimeType = '{FOLDER_MIME}' and trashed = false",
                         "fields": "files(id)", "pageSize": 1})["files"]
    if found:
        return found[0]["id"]
    return call(token, "POST", "https://www.googleapis.com/drive/v3/files",
                params={"fields": "id"}, json={"name": FOLDER, "mimeType": FOLDER_MIME})["id"]


def safe_name(name):
    return re.sub(r"[\x00-\x1f/\\]", " ", name).strip()[:120] or "first-run draft"


def deliver(tenant, filename, content, mime, properties):
    token = account_token(tenant)
    boundary = uuid.uuid4().hex
    metadata = {"name": safe_name(filename), "parents": [folder(token)],
                "appProperties": {k: str(v)[:120] for k, v in properties.items()}}
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
            + json.dumps(metadata).encode()
            + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode() + content
            + f"\r\n--{boundary}--\r\n".encode())
    return call(token, "POST", "https://www.googleapis.com/upload/drive/v3/files",
                params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                headers={"Content-Type": f"multipart/related; boundary={boundary}"}, content=body)
