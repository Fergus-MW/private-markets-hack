"""Email conversation coordinator. Workflow execution runs in a separate task."""
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

I'll tell you when I start and send another update when the task finishes, including if it is blocked or fails. Draft outputs and legal terms still need human review. I can also check ingestion progress and retry ingestion if something goes wrong. I will notify you when your files are ingested and your knowledge graph is ready.
"""


class Engine:
    def __init__(self, repo, queue, mailer, graph_factory, router, ingestion=None):
        self.repo, self.queue, self.mailer = repo, queue, mailer
        self.graph_factory, self.router = graph_factory, router
        self.ingestion = ingestion or Ingestion(repo, queue)

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
                        send("start_sent", f"I'm starting the {label} for project {call['args']['project_id']}. I've assigned it to the workflow agent team and will reply again when it finishes.\n\nTask: {workflow_id}")
                        self.repo.create("jobs", workflow_id, {
                            "kind": "workflow", "email": job["email"], "tenant": job["tenant"],
                            "message_id": job["message_id"], "tool_call": call,
                            "thread_id": job.get("thread_id", identifier),
                        })
                        self.queue(workflow_id)
                        save(tool_result={"job_id": workflow_id, "status": "queued"})
                else:
                    send("reply_sent", decision["reply"])
            elif job["kind"] == "ingestion":
                send("start_sent", "I'm starting ingestion of your connected Drive and Gmail files and generating your knowledge graph. I'll email you when it finishes, or if anything prevents it from completing.")
                if "result" not in job:
                    save(result=self.ingestion.advance(identifier, job, save))
                send("finish_sent", job["result"]["summary"])
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
                    {"request": job.get("text"), "decision": job.get("decision"), "result": job.get("result")})
            save(done=True)
        finally:
            self.repo.save(identifier, token, lease_until=0)
