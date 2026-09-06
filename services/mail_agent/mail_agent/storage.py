"""Durable jobs with fenced leases; Cloud Tasks carries only the job ID."""
import hashlib
import os
import time
import uuid

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore, tasks_v2


def key(*parts):
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def tenant(email):
    return "u-" + hashlib.sha256(email.lower().encode()).hexdigest()[:16]


class Busy(Exception):
    pass


class Repository:
    def __init__(self):
        self.db = firestore.Client(database=os.environ.get("MAIL_DATABASE", "agent-mail"))

    def get(self, collection, identifier):
        return self.db.collection(collection).document(identifier).get().to_dict()

    def list(self, collection, limit=50):
        # ponytail: bounded scan, adequate for this account count. Index by
        # sender identity if inbound client mail ever outgrows one page.
        return [doc.to_dict() for doc in self.db.collection(collection).limit(limit).stream()]

    def create(self, collection, identifier, value):
        try:
            self.db.collection(collection).document(identifier).create(value)
        except AlreadyExists:
            pass

    def reserve_ingestion(self, account, identifier, retry=False):
        ref = self.db.collection("accounts").document(key(account["email"]))
        job_ref = self.db.collection("jobs").document(identifier)

        @firestore.transactional
        def reserve(tx):
            stored = ref.get(transaction=tx).to_dict()
            if not stored or stored["tenant"] != account["tenant"] or account["tenant"] != tenant(account["email"]):
                raise ValueError("Unknown ingestion account")
            previous = stored.get("ingestion_job")
            current = self.db.collection("jobs").document(previous).get(transaction=tx).to_dict() if previous else None
            if previous and (previous == identifier or not retry or not current or not current.get("done") or not current.get("result", {}).get("retry_safe", False)):
                return previous
            tx.create(job_ref, {"kind": "ingestion", "email": account["email"], "tenant": account["tenant"],
                               "message_id": account.get("message_id"), "thread_id": account.get("thread_id"),
                               "subject": "Your knowledge graph: ingestion update", "created_at": time.time()})
            tx.update(ref, {"ingestion_job": identifier})
            return identifier

        return reserve(self.db.transaction())

    def claim(self, identifier):
        ref = self.db.collection("jobs").document(identifier)
        token = uuid.uuid4().hex

        @firestore.transactional
        def acquire(tx):
            job = ref.get(transaction=tx).to_dict()
            if not job or job.get("done"):
                return None
            if job.get("lease_until", 0) > time.time():
                raise Busy()
            tx.update(ref, {"lease": token, "lease_until": time.time() + 1800})
            return {**job, "lease": token}

        return acquire(self.db.transaction())

    def remember(self, thread_id, job_id, event):
        ref = self.db.collection("threads").document(thread_id)

        @firestore.transactional
        def append(tx):
            thread = ref.get(transaction=tx).to_dict() or {}
            events = thread.get("events", [])
            if not any(item["job_id"] == job_id for item in events):
                tx.set(ref, {"events": (events + [{"job_id": job_id, **event}])[-8:]})

        append(self.db.transaction())

    def save(self, identifier, token, **updates):
        ref = self.db.collection("jobs").document(identifier)

        @firestore.transactional
        def update(tx):
            job = ref.get(transaction=tx).to_dict()
            if job.get("lease") != token:
                raise Busy()
            tx.update(ref, updates)

        update(self.db.transaction())


def enqueue(identifier):
    client = tasks_v2.CloudTasksClient()
    queue = os.environ["MAIL_QUEUE"]
    url = os.environ["MAIL_SERVICE_URL"].rstrip("/")
    try:
        client.create_task(parent=queue, task={
            "name": queue + "/tasks/" + identifier,
            "dispatch_deadline": {"seconds": 1800},
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": url + "/jobs/" + identifier,
                "oidc_token": {"service_account_email": os.environ["MAIL_TASK_ACCOUNT"], "audience": url},
            },
        })
    except AlreadyExists:
        pass
