"""Email conversation coordinator. Workflow execution runs in a separate task."""
import json
import time
from urllib.parse import quote

from .storage import key
from .ingestion import Ingestion

LINKS = {"get_project_graph_link": "project graph",
         "get_workspace_graph_link": "high-level knowledge graph",
         "get_qc_dashboard_link": "QC gate dashboard"}

WORKFLOWS = {"trigger_qc_gate": ("qc", "QC gate"),
             "trigger_first_run": ("first-run", "first run-through"),
             "explain_run": ("explain", "explanation of your recorded results"),
             "answer_project_question": ("answer", "answer to your project question")}

WELCOME = """Hello, and welcome. I am your private markets agent. You give me work by
replying to this address, and you never need to sign in to do it.

WHAT IS HAPPENING RIGHT NOW

I am reading the Google Drive and Gmail files you just gave me access to. I
build them into a knowledge graph holding the people, companies, funds,
documents and quarterly projects in your work, and the links between them. I
will email you the moment it is ready. Write to me before then and I may tell
you I am still reading.

Ask "how is my ingestion going?" whenever you like. If it looks stuck, say
"retry ingestion" and I will start it again.

HOW I WORK

I scope every piece of work to a project: one fund, one quarter, one job. Once
your graph is built, the fund name and the quarter are usually all I need to
find the right one.

Each project gets its own private workspace. I copy its documents in, and I
keep every output I produce there for good, linked back to the evidence behind
it. That way I can always show you where a number came from.

THE TWO KINDS OF WORK I DO

1) FIRST RUN-THROUGH, for a deliverable that does not exist yet.

   A production agent drafts it as a workbook and writes down the delivery
   rules it inferred, quoting the source text behind each one. A second agent
   reviews that draft against the same evidence and lists what remains
   unresolved. You get the workbook, the rules, and a straight account of what
   is missing. Where I cannot find a number, I say so rather than filling the
   gap with a plausible one. I also put the workbook in a "Private markets
   drafts" folder in your Google Drive.

   Say: "Do a first run-through for Fund A, Q2 2026."

2) QC GATE, for a deliverable that already exists and needs checking.

   Here a fixed, version-pinned checker runs your loader file or terms schedule
   against the source data and returns a result for each check. The agents
   around it choose the inputs and explain the outcome. They have no power to
   overrule a finding. Terms checks also need a named person to ratify the
   terms snapshot before anything runs.

   Say: "Run QC for Fund A, Q2 2026."

   Afterwards I will send you a dashboard link showing every check with its
   tier, the evidence behind it, the passes as well as the failures, and the
   amount at stake.

WHAT ELSE YOU CAN ASK ME

* "What did the QC gate find?" I read back what is on the record instead of
  running anything again. The same goes for "why was that blocked?"
* "What is the management fee basis for Fund A?" I answer from that project's
  own documents and quote the source. If they do not answer it, I tell you.
* "How is that task going?" I report live status while something runs. Add
  "with logs" for phase-by-phase detail.
* "Show me the graph for Fund A" or "show me my knowledge graph" and I will
  send a link. You will need to be signed in to open it.

HOW TO TALK TO ME

Reply in plain English. Four things help:

* Name the fund and the quarter. "Fund A, Q2 2026" is enough.
* Send one request per email. Ask for two workflows at once and I will ask you
  to pick.
* If I am missing an input, I name it exactly. Send it and ask me again.
* If your intent is ambiguous, I ask you rather than guess.

I tell you when I have started something, and I write again when it finishes,
including when it comes back blocked or failed. I would rather report a blocked
run than hand you a confident wrong answer.

One more thing. If someone I already recognise from your documents emails me
directly, I file their message and any attachments into your graph and refresh
whichever project it relates to. You do not need to forward things twice.

WHAT I WILL NOT DO

I approve nothing. A draft stays a draft, and legal terms always need a human
to sign off. A completed QC run means the checks ran and the findings are
yours to judge. It does not mean the work passed.
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
                        save(decision={"reply": "I could not read your request or load your projects, even after retrying. I have started no work. Please send it again in a few minutes."})
                decision = job["decision"]
                if "tool_call" in decision:
                    call = decision["tool_call"]
                    if call["name"] == "check_ingestion_status":
                        send("start_sent", "Thank you. I am checking your ingestion progress now and will tell you whether your knowledge graph is ready.")
                        if "tool_result" not in job:
                            save(tool_result={"summary": self.ingestion.status(job)})
                        send("finish_sent", job["tool_result"]["summary"])
                    elif call["name"] == "retry_ingestion":
                        if "tool_result" not in job:
                            run_id = self.ingestion.request(job, identifier, retry=True)
                            save(tool_result={"job_id": run_id})
                        send("reply_sent", "Thank you. I have registered your ingestion request. If a run is already going I will reuse it rather than "
                             "duplicate the work. I will write when a new run starts, and again when it finishes.\n\n" + self.ingestion.status(job))
                    elif call["name"] == "check_workflow_status":
                        if "tool_result" not in job:
                            args = call["args"]
                            worker = self.repo.get("jobs", args["job_id"])
                            valid = (worker and worker.get("kind") == "workflow"
                                     and worker.get("tenant") == job["tenant"]
                                     and worker.get("tool_call", {}).get("args", {}).get("project_id") == args["project_id"])
                            if not valid:
                                result = {"status": "not_found", "phase": "not_found", "trace": [],
                                          "result": {"summary": "I could not find that task in this account and project. Please check the Task ID against the email I sent when the task started."}}
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
                    elif call["name"] in LINKS:
                        label = LINKS[call["name"]]
                        send("start_sent", f"Of course. I am fetching your private {label} link now.")
                        if "tool_result" not in job:
                            client = self.graph_factory(job)
                            link = (client.dashboard(call["args"]["project_id"])
                                    if call["name"] == "get_qc_dashboard_link"
                                    else client.visualization(call["args"].get("project_id")))
                            save(tool_result={"link": link})
                        send("finish_sent", f"Here is your private {label}:\n\n{job['tool_result']['link']}\n\nTo open it, sign in with the same Google account you connected to me.")
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
                        send("start_sent", f"Thank you. I am starting the {label} for project {call['args']['project_id']} and have handed it to the workflow agent team. I will write again as soon as it finishes.\n\nTask: {workflow_id}")
                        self.queue(workflow_id)
                else:
                    send("reply_sent", decision["reply"])
            elif job["kind"] == "ingestion":
                send("start_sent", "I am starting on your connected Drive and Gmail files now and will build your knowledge graph from them. I will email you when it finishes, or sooner if anything stops it completing.")
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
                        result = {"status": "failed", "summary": "The workflow service did not return a result, even after retrying. A run may still be finishing, so please check the project's run history before you ask for another."}
                    save(result=result)
                result = job["result"]
                text = f"Your task has finished with status: {result['status']}.\n\n{result['summary']}\n\nTask: {identifier}"
                if result.get("dashboard"):
                    text += ("\n\nEvery check, its tier, its evidence and the amount at stake are on your QC gate "
                             "dashboard (available after signing in):\n" + result["dashboard"])
                if result.get("artifacts"):
                    text += "\n\nProject artifacts, available once you are signed in:\n" + "\n".join(
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
