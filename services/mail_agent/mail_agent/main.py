import os
import re
import time
from email.utils import parseaddr
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from svix.webhooks import Webhook, WebhookVerificationError

from .clients import GraphClient, Mailer, route
from .engine import Engine
from .ingestion import Ingestion
from .storage import Busy, Repository, enqueue, key, tenant

app = FastAPI(title="Private markets email agent")


@lru_cache
def repository():
    return Repository()


class Signup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@app.get("/healthz")
def health():
    return {"status": "ok"}


# Service is private. Only the frontend and task identity have run.invoker.
# Signup is called after Google has verified the OAuth email address.
@app.post("/signup", status_code=202)
def signup(request: Signup):
    email = request.email.lower()
    account = {"email": email, "tenant": tenant(email)}
    repo = repository()
    repo.create("accounts", key(email), account)
    identifier = key("welcome-v1", email)
    repo.create("jobs", identifier, {"kind": "welcome", **account})
    enqueue(identifier)
    Ingestion(repo, enqueue).request(account, "signup-v1")
    return {"accepted": True}


# Same private trust boundary as signup: the frontend proves the session and
# sends the Google-verified email; this service never trusts a browser directly.
@app.post("/ingestion/status")
def ingestion_status(request: Signup):
    email = request.email.lower()
    repo = repository()
    if not repo.get("accounts", key(email)):
        raise HTTPException(404, "No account for this address")
    return Ingestion(repo, enqueue).report({"email": email, "tenant": tenant(email)})


def recipients(message):
    """Addresses the message was sent to. Used only to order candidate accounts,
    never to grant access: the graph still has to know the sender."""
    found = []
    for field in ("to", "cc"):
        for value in message.get(field) or []:
            address = parseaddr(value)[1].lower()
            if address and address not in found:
                found.append(address)
    return found[:20]


# Cloud Scheduler only. New mail in a user's own mailbox raises no event we can
# receive, so ingestion is polled: a run per window picks up whatever arrived.
# reserve_ingestion refuses to start while a run is in flight or when the last
# one ended unconfirmed, so a stuck account is never re-run behind its own back.
@app.post("/ingestion/poll")
def poll():
    repo = repository()
    window = max(60, int(os.environ.get("INGESTION_POLL_WINDOW_SECONDS", "3600")))
    request_id = "poll-%d" % (time.time() // window)
    started, held, skipped = 0, 0, 0
    # ponytail: one bounded page, matching engine.py. Paginate if accounts outgrow it.
    for account in repo.list("accounts", limit=int(os.environ.get("INGESTION_POLL_LIMIT", "500"))):
        if not account.get("email") or not account.get("tenant"):
            skipped += 1
            continue
        expected = key("ingestion", account["tenant"], request_id)
        # A run for this window may already exist from a scheduler retry, and
        # reserve_ingestion returns it unchanged. Only a change is a new run.
        before = (repo.get("accounts", key(account["email"])) or {}).get("ingestion_job")
        try:
            identifier = Ingestion(repo, enqueue).request(account, request_id, retry=True)
        except ValueError:
            # Account failed its own tenant check; skip it, do not abort the sweep.
            skipped += 1
            continue
        # A returned identifier that is not the one for this window means the
        # account already has a run that reserve_ingestion declined to replace.
        if identifier == expected and before != expected:
            started += 1
        else:
            held += 1
    return {"started": started, "held": held, "skipped": skipped, "window": window}


@app.post("/webhook", status_code=202)
async def webhook(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 512 * 1024:
            raise HTTPException(413, "Webhook too large")
    try:
        event = Webhook(os.environ["AGENTMAIL_WEBHOOK_SECRET"]).verify(bytes(body), dict(request.headers))
    except (WebhookVerificationError, ValueError):
        raise HTTPException(401, "Invalid webhook signature") from None
    if event.get("event_type") != "message.received":
        return {"ignored": True}
    message = event.get("message", {})
    if message.get("inbox_id") != os.environ["AGENTMAIL_INBOX_ID"]:
        return {"ignored": True}
    if set(message.get("labels", [])) & {"spam", "blocked", "unauthenticated", "sent"}:
        return {"ignored": True}
    headers = {name.lower(): value for name, value in (message.get("headers") or {}).items()}
    if headers.get("auto-submitted", "no").lower() != "no" or headers.get("precedence", "").lower() in {"bulk", "list", "junk"}:
        return {"ignored": True}
    email = parseaddr(message.get("from") or "")[1].lower()
    if not email or email == os.environ["AGENTMAIL_INBOX_ID"].lower():
        return {"ignored": True}
    account = repository().get("accounts", key(email))
    message_id = message.get("message_id")
    if not isinstance(message_id, str) or not message_id:
        raise HTTPException(400, "Missing message ID")
    if not account:
        # Mail from a client the graph already knows is evidence, not instruction.
        # It is queued for ingestion only and never reaches the assistant router;
        # the worker drops it if no graph actually knows the sender.
        identifier = key("client-mail-v1", message["inbox_id"], message_id)
        repository().create("jobs", identifier, {
            "kind": "client_mail", "sender": email, "message_id": message_id,
            "thread_id": message.get("thread_id") or message_id,
            "subject": (message.get("subject") or "")[:1000],
            "recipients": recipients(message)})
        enqueue(identifier)
        return {"accepted": True}
    # AgentMail extracted_text excludes quoted thread history. Do not pull old
    # instructions from the full quoted body when stripped text is available.
    text = message.get("extracted_text") or message.get("text") or ""
    if "extracted_text" in message:
        text = message["extracted_text"] or ""
    identifier = key("incoming", message["inbox_id"], message_id)
    repository().create("jobs", identifier, {"kind": "incoming", **account,
        "message_id": message_id, "thread_id": message.get("thread_id") or message_id,
        # Signals for filing the sender's own mail as evidence. Kept raw so the
        # rule can change without stranding jobs queued under the old one.
        "subject": (message.get("subject") or "")[:1000],
        "attachments": len(message.get("attachments") or []),
        "text": ((message.get("subject") or "") + "\n\n" + text)[:24000]})
    enqueue(identifier)
    return {"accepted": True}


@app.post("/jobs/{identifier}")
def process(identifier: str):
    if not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise HTTPException(422, "Invalid job ID")
    try:
        Engine(repository(), enqueue, Mailer(), GraphClient, route).process(identifier)
    except Busy:
        raise HTTPException(409, "Job already running; retry later") from None
    return {"ok": True}
