# AgentMail release — 6 September 2026

Inbox: `zeroadmin@agentmail.to`. The existing inbox-scoped API key is sufficient; use inbox-scoped webhook management rather than organization-level creation. Webhook ID: `ep_3IweuxjGEBqsM6yz5TYKTXzhlg7`. The API key and webhook signing secret live in Secret Manager, never application source or build uploads.

The applied bootstrap created the named `agent-mail` Firestore database, `agent-mail` Cloud Tasks queue, runtime/task service accounts, API enablement, model permissions, secret containers and scoped access. The existing frontend service account and its two secret containers were imported rather than recreated. The checked-in workflow namespace migration completed without replacing the VM or changing the canonical database.

Tested release images:

| Service | Digest |
| --- | --- |
| Mail | `sha256:0fb7c4d47605deb422bfa700bf6c2ec8b52e3a50c567b70388b7079f615ef813` |
| Workflow ingestion | `sha256:d481ea3e153d23429b1dcb326d9586256a8665b77f16cf13d8f61e951829e60b` |
| Frontend | `sha256:e4f8ee11b540b955399a08f670332a9649a116e2a951dc9e5252fe2c663ae2d0` |
| Connector compatibility | `sha256:b2e1486bd48198a9233def767d02200a8164e6d62e045666de39076cf006f353` |

All images are under `europe-west2-docker.pkg.dev/private-markets-hack/services/` (`agent-mail`, `ingestion`, `frontend`, `connectors`). The workflow and frontend releases were built from a stable snapshot of the original repository plus the mail integration, because independent frontend work was changing during the initial source upload. That concurrent UI work remains in the shared workspace and is not part of these release images.

Validation: 29 mail tests; 28 connector tests; 25 frontend tests and production build; 41 ingestion tests with six environment-dependent integration cases skipped. The final images passed their Cloud Build tests. Live synthetic Vertex requests verified the actual workflow, ingestion, project-question and graph-link function calls and rule generation with validated source quotations. Worker JSON is validated locally because the heterogeneous worksheet schema was rejected by Vertex constrained decoding.

Live rollout completed. The private worker returned 200 after accessing its Firestore database; unauthenticated mail-service signup returned 403. A signed, unregistered-sender event passed through the public frontend and returned 202 without sending mail. An invalid signature returned 401, and unauthenticated frontend graph access returned 401. `/api/session` reports signup configured. A no-op Cloud Task completed with `status: OK`; its first attempt encountered IAM propagation delay and the queue retried successfully. External `/healthz` requests returned a Google 404 page; workflow availability was instead verified with `/formats` (200) and Cloud Run revision readiness.

No real welcome or workflow-result email was sent as a smoke test. Sign in/reconnect through the frontend to exercise the real welcome flow, then reply from that verified email account. Signup starts account-scoped Drive and Gmail ingestion. The agent reports progress through the `check_ingestion_status` tool and can launch a safe retry through `retry_ingestion`. It sends a ready notification only after both connector executions complete and the graph-producing pipeline confirms at least one ingested or current source.

The mail router also supports `answer_project_question`, `get_project_graph_link` and `get_workspace_graph_link`. Project answers run against bounded project-local evidence and validate exact source quotations. Visualization links point to the private frontend viewer and contain no access credential; the frontend session selects the account graph. A canonical scoped project view is available before the first workflow, and completed workflows enrich it with project-local run and artifact nodes.

The existing `connector-team-drive` job was updated to the tested signed-identity worker. Its original credential selection and schedule were retained; no source scan was manually started. Legacy service credentials ingest into their service tenant. New accounts use the verified account's OAuth secret and isolated archive prefix. Inbound AgentMail attachments are not imported by the mail service.

Use `services/mail_agent/README.md` for operation and repeatable setup. Preserve the local `agentmail.auto.tfvars.json` configuration, including its image and frontend origin. The setup script merges updates into it. The pre-existing externally provisioned frontend/job resources require adoption before a full Terraform application rollout; the mail rollout uses reviewed targeted plans and explicit image updates.
