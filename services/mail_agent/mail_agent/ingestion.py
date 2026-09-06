"""Account-scoped connector execution and durable ingestion monitoring."""
import os
import re
import time
from urllib.parse import quote

import google.auth
from google.auth.transport.requests import AuthorizedSession

from .storage import Busy, key, tenant

PROVIDERS = ("drive", "gmail")


class ConnectorClient:
    def __init__(self):
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.session = AuthorizedSession(credentials)
        self.job = os.environ["INGESTION_CONNECTOR_JOB"]
        self.bucket = os.environ["INGESTION_STATUS_BUCKET"]

    def executions(self, run_id, provider):
        result, page = [], None
        while True:
            response = self.session.get(f"https://run.googleapis.com/v2/{self.job}/executions",
                params={"pageSize": 100, **({"pageToken": page} if page else {})}, timeout=30)
            response.raise_for_status()
            data = response.json()
            for execution in data.get("executions", []):
                env = {v["name"]: v.get("value") for c in execution.get("template", {}).get("containers", []) for v in c.get("env", [])}
                if env.get("INGESTION_RUN_ID") == run_id and env.get("CONNECTOR_PROVIDER") == provider:
                    result.append(execution)
            page = data.get("nextPageToken")
            if not page:
                return result

    def start(self, account, run_id, provider):
        if account["tenant"] != tenant(account["email"]) or not re.fullmatch(r"[a-f0-9]{64}", run_id) or provider not in PROVIDERS:
            raise ValueError("Invalid ingestion identity")
        project = os.environ["GOOGLE_CLOUD_PROJECT"]
        env = {
            "CONNECTOR_SECRET": f"projects/{project}/secrets/connector-{account['tenant']}-oauth/versions/latest",
            "CONNECTOR_PREFIX": account["tenant"], "CONNECTOR_PROVIDER": provider,
            "SOURCE_QUERY": "", "DRIVE_ID": "", "INGESTION_RUN_ID": run_id,
            "GRAPH_MULTI_USER": "true", "GRAPH_USE_GEMINI": "true",
        }
        response = self.session.post(f"https://run.googleapis.com/v2/{self.job}:run", json={
            "overrides": {"containerOverrides": [{"env": [{"name": k, "value": v} for k, v in env.items()]}],
                          "taskCount": 1, "timeout": "3600s"}}, timeout=30)
        response.raise_for_status()
        return response.json().get("name")

    def progress(self, account, run_id, provider):
        name = f"ingestion-status/{account['tenant']}/{run_id}/{provider}.json"
        response = self.session.get(f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{quote(name, safe='')}",
                                    params={"alt": "media"}, timeout=30)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        if (data.get("tenant"), data.get("run_id"), data.get("provider")) != (account["tenant"], run_id, provider):
            raise ValueError("Mismatched progress identity")
        return data


class Ingestion:
    def __init__(self, repo, queue, client=None):
        self.repo, self.queue, self.client = repo, queue, client

    def request(self, account, request_id, retry=False):
        identifier = self.repo.reserve_ingestion(account, key("ingestion", account["tenant"], request_id), retry)
        self.queue(identifier)
        return identifier

    def status(self, account):
        return self.report(account)["summary"]

    def report(self, account):
        """Structured form of status() for the browser; status() keeps the agent wording.

        Never infers readiness: the terminal state is whatever advance() recorded,
        so an unconfirmed run stays "unknown" here exactly as it does in email.
        """
        state = self.repo.get("accounts", key(account["email"])) or {}
        identifier = state.get("ingestion_job")
        job = self.repo.get("jobs", identifier) if identifier else None
        if not job:
            return {"state": "none", "done": False, "providers": [], "counts": {},
                    "summary": "No ingestion run has started yet. Ask me to start ingestion whenever you are ready."}
        if job.get("result"):
            result = job["result"]
            recorded = job.get("providers", {})
            providers = []
            for provider in PROVIDERS:
                finished = recorded.get(provider) or {}
                counts = finished.get("counts", {})
                providers.append({"provider": provider, "status": finished.get("status", "unknown"),
                                  "counts": counts, "checked": sum(counts.values())})
            return {"state": result["status"], "done": True, "providers": providers,
                    "counts": result.get("counts", {}), "summary": result["summary"]}
        details, providers = [], []
        for provider in PROVIDERS:
            progress = self.connector.progress(account, identifier, provider)
            if progress:
                counts = progress.get("counts", {})
                count = sum(counts.values())
                providers.append({"provider": provider, "status": progress["status"],
                                  "counts": counts, "checked": count})
                details.append(f"{provider.title()}: {progress['status']}, {count} items checked")
            else:
                providers.append({"provider": provider, "status": "queued", "counts": {}, "checked": 0})
                details.append(f"{provider.title()}: queued or starting")
        return {"state": "running", "done": False, "providers": providers, "counts": {},
                "summary": "Your ingestion is still running. " + "; ".join(details) + ". I will email you as soon as it finishes."}

    @property
    def connector(self):
        if self.client is None:
            self.client = ConnectorClient()
        return self.client

    def advance(self, identifier, job, save):
        providers = dict(job.get("providers", {}))
        for provider in PROVIDERS:
            state = dict(providers.get(provider, {}))
            if state.get("finished"):
                continue
            executions = self.connector.executions(identifier, provider)
            if not executions:
                if not state.get("launching_at"):
                    state["launching_at"] = time.time()
                    providers[provider] = state
                    save(providers=providers)
                    # Persist intent before the non-idempotent Run API. After a
                    # timeout/crash, reconcile execution overrides; never POST twice.
                    operation = self.connector.start(job, identifier, provider)
                    state["operation"] = operation
                    providers[provider] = state
                    save(providers=providers)
                    raise Busy()
                elif time.time() - state["launching_at"] > 900:
                    return {"status": "unknown", "retry_safe": False,
                            "summary": "I could not confirm whether the ingestion worker started, so I have not launched a duplicate. Someone will need to check the execution before you retry. Until then I cannot tell you your knowledge graph is ready."}
                raise Busy()
            if any(not execution.get("completionTime") for execution in executions):
                raise Busy()
            progress = self.connector.progress(job, identifier, provider)
            succeeded = all(execution.get("succeededCount", 0) == execution.get("taskCount", 1) for execution in executions)
            state.update(finished=True, status=(progress["status"] if succeeded and progress and progress["status"] in {"completed", "partial", "empty"} else "failed"),
                         counts=(progress or {}).get("counts", {}))
            providers[provider] = state
            save(providers=providers)
        counts = {}
        for state in providers.values():
            for name, count in state.get("counts", {}).items():
                counts[name] = counts.get(name, 0) + count
        total = counts.get("ingested", 0) + counts.get("unchanged_ingested", 0)
        statuses = {state["status"] for state in providers.values()}
        successful = bool(statuses) and statuses <= {"completed", "empty"}
        if successful and total:
            return {"status": "completed", "retry_safe": True, "counts": counts,
                    "summary": f"Good news. Your files are ingested and your knowledge graph is built and ready to use. Drive and Gmail both finished, covering {total} items that were ingested or already up to date. You can now ask me to run the QC gate or a first run-through for any project."}
        if successful:
            return {"status": "empty", "retry_safe": True, "counts": counts,
                    "summary": "The Drive and Gmail scans finished, but they found no supported files to ingest, so your knowledge graph is not ready yet. Add some source files and ask me to retry ingestion."}
        return {"status": "failed" if "failed" in statuses else "partial", "retry_safe": True, "counts": counts,
                "summary": f"Ingestion has finished, but not cleanly. I ingested or confirmed {total} items, and some files or a connector did not complete, so your knowledge graph is not fully ready. Reply 'retry ingestion' and I will pick up where I left off, reusing the files that already succeeded. If your access has expired, please reconnect your Google account first."}
