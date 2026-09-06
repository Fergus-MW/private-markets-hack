"""Email conversation coordinator. Workflow execution runs in a separate task."""
import json
import time
from urllib.parse import quote

from .storage import key
from .ingestion import Ingestion

WORKFLOWS = {"trigger_qc_gate": ("qc", "QC gate"),
             "trigger_first_run": ("first-run", "first run-through"),
             "explain_run": ("explain", "explanation of your recorded results"),
             "answer_project_question": ("answer", "answer to your project question")}

WELCOME = """Hello, and welcome! I'm your private markets agent.

I can coordinate two kinds of work:
• QC gate: check an existing loader or terms/side-letter schedule against project evidence.
• First run-through: ask a production agent team to attempt draft deliverables and derive cited delivery rules, highlighting missing inputs and decisions.

Reply with the fund/project, quarter and which workflow you want. For example: ‘Run QC for Fund A, Q2 2026’ or ‘Do a first run-through for Fund A, Q2 2026’.

You can also ask me about work I've already done — 'what did the QC gate find?' — and I'll read back the recorded results rather than starting anything new.

Ask me questions about a project, or ask to see its private graph. I can also send your high-level knowledge graph showing connected people, companies, funds and sources.

I'll tell you when I start and send another update when the task finishes, including if it is blocked or fails. While a task is running, ask for its status or request verbose logs and tracing for phase-by-phase progress. Draft outputs and legal terms still need human review. I can also check ingestion progress and retry ingestion if something goes wrong. I will notify you when your files are ingested and your knowledge graph is ready.
"""


def workflow_status_text(identifier, status, verbose=False):
    state = status.get("status", "unknown")
    phase = status.get("phase")
    text = f"Task {identifier} is {state}."
    if phase and phase != state:
        text += f" Current phase: {phase}."
    output = status.get("output") or status.get("result") or {}
    if output.get("summary"):
        text += "\n\n" + output["summary"]
    artifacts = output.get("artifacts") or {}
    if artifacts:
        text += "\n\nArtifacts available after signing in:\n" + "\n".join(
            f"{name}: {url}" for name, url in artifacts.items())
    if status.get("trace_unavailable"):
        text += "\n\nDetailed execution tracing is temporarily unavailable; the durable task state above is still current."
    if verbose:
        trace = status.get("trace") or []
        if trace:
            lines = []
            for event in trace:
                line = f"- {event.get('at', 'unknown time')} | {event.get('phase', 'unknown')} | {event.get('message', '')}"
                if event.get("details"):
                    line += " | " + json.dumps(event["details"], sort_keys=True, default=str)
                lines.append(line)
            text += "\n\nVerbose execution trace:\n" + "\n".join(lines)
        else:
            text += "\n\nNo execution trace has been recorded yet; the task may still be waiting for a worker."
    return text


class Engine:
    def __init__(self, repo, queue, mailer, graph_factory, router, ingestion=None):
        self.repo, self.queue, self.mailer = repo, queue, mailer
        self.graph_factory, self.router = graph_factory, router
        self.ingestion = ingestion or Ingestion(repo, queue)

    def client_mail(self, job):
        """Ingest one inbound client message into every graph that already knows the
        sender, refreshing the projects it relates to. Re-running is safe: sources are
        content-addressed and materialization is idempotent."""
        raw = self.mailer.raw(job["message_id"])
        accounts = self.repo.list("accounts")
        addressed = set(job.get("recipients", []))
        # Check accounts on the message first; the rest still have to know the sender.
        accounts.sort(key=lambda account: account.get("email") not in addressed)
        ingested, seen = [], set()
        for account in accounts:
            if not account.get("tenant") or account["tenant"] in seen:
                continue
            seen.add(account["tenant"])
            graph = self.graph_factory(account)
            if not graph.call("GET", "/mail/senders/" + quote(job["sender"], safe=""))["known"]:
                continue
            result = graph.upload("/mail/ingest", {"sender": job["sender"],
                "external_id": job["message_id"], "subject": job.get("subject", "")}, raw)
            ingested.append({"tenant": account["tenant"], "source_id": result["source_id"],
                             "attachments": result["attachments"], "projects": result["projects"]})
        return {"ingested": ingested, "graphs_checked": len(seen)}

    def process(self, identifier):
        job = self.repo.claim(identifier)
        if job is None:
            return
        token = job["lease"]

        def save(**updates):
            self.repo.save(identifier, token, **updates)
            job.update(updates)

        def send(stage, text):
            if not job.get(stage):
                self.mailer.send({**job, "delivery_key": key(identifier, stage)}, text)
                save(**{stage: True})

        try:
            if job["kind"] == "welcome":
                send("welcome_sent", WELCOME)
            elif job["kind"] == "incoming":
                if "decision" not in job:
                    try:
                        graph = self.graph_factory(job)
                        history = self.repo.get("threads", key(job["tenant"], job.get("thread_id", identifier))) or {}
                        try:
                            projects = graph.projects()
                        except Exception:
                            projects = []  # Recovery must work when the graph is unavailable.
                        save(decision=self.router(job["text"], projects, history.get("events", [])))
                    except Exception:
                        attempts = job.get("attempts", 0) + 1
                        save(attempts=attempts)
                        if attempts < 3:
                            raise
                        save(decision={"reply": "I couldn't interpret your request or load your projects after retrying. No workflow was started. Please try again shortly."})
                decision = job["decision"]
                if "tool_call" in decision:
                    call = decision["tool_call"]
                    if call["name"] == "check_ingestion_status":
                        send("start_sent", "I'm checking your ingestion progress and whether your knowledge graph is ready.")
                        if "tool_result" not in job:
                            save(tool_result={"summary": self.ingestion.status(job)})
                        send("finish_sent", job["tool_result"]["summary"])
                    elif call["name"] == "retry_ingestion":
                        if "tool_result" not in job:
                            run_id = self.ingestion.request(job, identifier, retry=True)
                            save(tool_result={"job_id": run_id})
                        send("reply_sent", "I've registered your ingestion request. An active run will be reused to avoid duplicate work. "
                             "I'll send a start update for a new run and another update when it finishes.\n\n" + self.ingestion.status(job))
                    elif call["name"] == "check_workflow_status":
                        if "tool_result" not in job:
                            args = call["args"]
                            worker = self.repo.get("jobs", args["job_id"])
                            valid = (worker and worker.get("kind") == "workflow"
                                     and worker.get("tenant") == job["tenant"]
                                     and worker.get("tool_call", {}).get("args", {}).get("project_id") == args["project_id"])
                            if not valid:
                                result = {"status": "not_found", "phase": "not_found", "trace": [],
                                          "result": {"summary": "I couldn't find that task in this account and project. Check the Task ID from the start email."}}
                            else:
                                result = {"status": "queued", "phase": "queued", "trace": []}
                                if worker.get("result"):
                                    result = {"status": worker["result"].get("status", "unknown"),
                                              "phase": "finished", "result": worker["result"], "trace": []}
                                try:
                                    live = self.graph_factory(job).call(
                                        "GET", f"/projects/{args['project_id']}/agents/jobs/{args['job_id']}")
                                    result = live
                                except Exception:
                                    # The project run is created only after a queued worker begins.
                                    # Firestore still provides an authoritative queued/terminal state.
                                    if worker.get("lease_until", 0) > time.time() and not worker.get("result"):
                                        result.update(status="running", phase="starting")
                                    if args.get("verbose") and result["status"] != "queued":
                                        result["trace_unavailable"] = True
                            save(tool_result=result)
                        send("reply_sent", workflow_status_text(call["args"]["job_id"], job["tool_result"],
                                                                 call["args"].get("verbose", False)))
                    elif call["name"] in {"get_project_graph_link", "get_workspace_graph_link"}:
                        label = "project graph" if call["name"] == "get_project_graph_link" else "high-level knowledge graph"
                        send("start_sent", f"I'm finding your private {label} visualization.")
                        if "tool_result" not in job:
                            link = self.graph_factory(job).visualization(call["args"].get("project_id"))
                            save(tool_result={"link": link})
                        send("finish_sent", f"Here is your private {label}:\n\n{job['tool_result']['link']}\n\nSign in with the same Google account you connected to the agent to open it.")
                    else:
                        workflow_id = key(identifier, "workflow")
                        label = WORKFLOWS[call["name"]][1]
                        self.repo.create("jobs", workflow_id, {
                            "kind": "workflow", "email": job["email"], "tenant": job["tenant"],
                            "message_id": job["message_id"], "tool_call": call,
                            "thread_id": job.get("thread_id", identifier),
                        })
                        save(tool_result={"job_id": workflow_id, "status": "queued"})
                        # Make a status follow-up resolvable before the start email
                        # can reach the recipient. Dispatch still occurs after mail.
                        self.repo.remember(key(job["tenant"], job.get("thread_id", identifier)), identifier,
                            {"request": job.get("text"), "decision": job.get("decision"),
                             "tool_result": job["tool_result"], "result": None})
                        send("start_sent", f"I'm starting the {label} for project {call['args']['project_id']}. I've assigned it to the workflow agent team and will reply again when it finishes.\n\nTask: {workflow_id}")
                        self.queue(workflow_id)
                else:
                    send("reply_sent", decision["reply"])
            elif job["kind"] == "ingestion":
                send("start_sent", "I'm starting ingestion of your connected Drive and Gmail files and generating your knowledge graph. I'll email you when it finishes, or if anything prevents it from completing.")
                if "result" not in job:
                    save(result=self.ingestion.advance(identifier, job, save))
                send("finish_sent", job["result"]["summary"])
            elif job["kind"] == "client_mail":
                # Silent by design: ingestion updates the graphs, it does not reply
                # to the client and never asks the assistant to interpret the mail.
                if "result" not in job:
                    save(result=self.client_mail(job))
            elif job["kind"] == "workflow":
                if "result" not in job:
                    call = job["tool_call"]
                    workflow = WORKFLOWS[call["name"]][0]
                    try:
                        result = self.graph_factory(job).call("POST", f"/projects/{call['args']['project_id']}/agents/{workflow}",
                            {"job_id": identifier, "instructions": call["args"].get("instructions", call["args"].get("question", ""))})
                    except Exception:
                        attempts = job.get("attempts", 0) + 1
                        save(attempts=attempts)
                        if attempts < 3:
                            raise
                        result = {"status": "failed", "summary": "The workflow service could not return a result after retrying. A run may still be finishing; check the project's run history before requesting another run."}
                    save(result=result)
                result = job["result"]
                text = f"The task finished with status: {result['status']}.\n\n{result['summary']}\n\nTask: {identifier}"
                if result.get("artifacts"):
                    text += "\n\nProject artifacts (available after signing in):\n" + "\n".join(
                        f"{name}: {url}" for name, url in result["artifacts"].items())
                send("finish_sent", text)
            else:
                raise ValueError("Unknown job type")
            if job["kind"] in {"incoming", "workflow"}:
                self.repo.remember(key(job["tenant"], job.get("thread_id", identifier)), identifier,
                    {"request": job.get("text"), "decision": job.get("decision"),
                     "tool_result": job.get("tool_result"), "result": job.get("result")})
            save(done=True)
        finally:
            self.repo.save(identifier, token, lease_until=0)
