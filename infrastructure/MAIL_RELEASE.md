# AgentMail release — 6 September 2026

Inbox: `zeroadmin@agentmail.to`. The existing inbox-scoped API key is sufficient; use inbox-scoped webhook management rather than organization-level creation. Webhook ID: `ep_3IweuxjGEBqsM6yz5TYKTXzhlg7`. The API key and webhook signing secret live in Secret Manager, never application source or build uploads.

The applied bootstrap created the named `agent-mail` Firestore database, `agent-mail` Cloud Tasks queue, runtime/task service accounts, API enablement, model permissions, secret containers and scoped access. The existing frontend service account and its two secret containers were imported rather than recreated. The checked-in workflow namespace migration completed without replacing the VM or changing the canonical database.

Tested release images:

| Service | Digest |
| --- | --- |
| Model gateway | `sha256:60104dd0274e90dfccd53b9f80f6da2e953c3f46f1c481bd296be30f1407b093` |
| Mail | `sha256:c8cab7476b43e8abe4b9152c715837b705fc2eb212f772a616db825cd270655e` |
| Workflow ingestion | `sha256:3e852b7e4629fbe94fefe9317a35419d3c79974310c6df8799428d92096492bd` |
| Frontend | `sha256:e4f8ee11b540b955399a08f670332a9649a116e2a951dc9e5252fe2c663ae2d0` |
| Connector compatibility | `sha256:b2e1486bd48198a9233def767d02200a8164e6d62e045666de39076cf006f353` |

All images are under `europe-west2-docker.pkg.dev/private-markets-hack/services/` (`model-gateway`, `agent-mail`, `ingestion`, `frontend`, `connectors`). The mail and workflow releases were built from one stable source snapshot.

All model-backed roles use `gemini-3.1-pro-preview`: mail routing and tool selection, QC planning and review, first-run production and review, workflow-result explanations, project Q&A, and unstructured graph extraction. Ingestion state checks and retries, deterministic QC checks, graph-link generation, and connector execution do not call a model.

The private model gateway is the only runtime identity with Vertex model access. Mail and workflow identities have gateway invoke access and no direct `roles/aiplatform.user` binding. Its single warm instance applies a shared TCP-style AIMD concurrency window, bounded retries, `Retry-After`/`RetryInfo` handling and exponential jitter. It renders the request once and sends identical bytes on retries. Project evidence precedes role-specific workflow instructions so implicit prefix caching survives transitions between agent roles. Cache-token and congestion-window telemetry excludes prompt content.

Validation: 4 gateway tests; 38 mail tests; 28 connector tests; 25 frontend tests and production build; 56 ingestion tests with six environment-dependent integration cases skipped. The final images passed their Cloud Build tests. A live repeated-prefix probe returned 12,276 cached tokens from a 25,004-token prompt on its second request. A separate live request returned `READY` from `gemini-3.1-pro-preview` through the gateway after direct caller permissions were removed. Worker JSON is validated locally because the heterogeneous worksheet schema was rejected by Vertex constrained decoding.

Live gateway rollout completed on `model-gateway-00001-ddf`; mail is on `agent-mail-00009-xvn` and workflow ingestion is on `document-ingestion-00011-qk5`, each serving 100% of traffic. The private worker returned 200 after accessing its Firestore database; unauthenticated mail-service signup returned 403. A signed, unregistered-sender event passed through the public frontend and returned 202 without sending mail. An invalid signature returned 401, and unauthenticated frontend graph access returned 401. `/api/session` reports signup configured. A no-op Cloud Task completed with `status: OK`; its first attempt encountered IAM propagation delay and the queue retried successfully. External `/healthz` requests returned a Google 404 page; workflow availability was instead verified with `/formats` (200) and Cloud Run revision readiness.

No real welcome or workflow-result email was sent as a smoke test. Sign in/reconnect through the frontend to exercise the real welcome flow, then reply from that verified email account. Signup starts account-scoped Drive and Gmail ingestion. The agent reports progress through the `check_ingestion_status` tool and can launch a safe retry through `retry_ingestion`. It sends a ready notification only after both connector executions complete and the graph-producing pipeline confirms at least one ingested or current source.

The mail router also supports `answer_project_question`, `get_project_graph_link` and `get_workspace_graph_link`. Project answers run against bounded project-local evidence and validate exact source quotations. Visualization links point to the private frontend viewer and contain no access credential; the frontend session selects the account graph. A canonical scoped project view is available before the first workflow, and completed workflows enrich it with project-local run and artifact nodes.

The existing `connector-team-drive` job was updated to the tested signed-identity worker. Its original credential selection and schedule were retained; no source scan was manually started. Legacy service credentials ingest into their service tenant. New accounts use the verified account's OAuth secret and isolated archive prefix. Inbound AgentMail attachments are not imported by the mail service.

Use `services/mail_agent/README.md` for operation and repeatable setup. Preserve the local `agentmail.auto.tfvars.json` configuration, including its image and frontend origin. The setup script merges updates into it. The pre-existing externally provisioned frontend/job resources require adoption before a full Terraform application rollout; the mail rollout uses reviewed targeted plans and explicit image updates.
