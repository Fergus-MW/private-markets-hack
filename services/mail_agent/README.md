# Email agent

The Google OAuth callback queues one welcome email and one account-scoped ingestion run per verified account. Users reply to the AgentMail inbox with their project, quarter and requested work. The coordinator uses actual Gemini function calls (`trigger_qc_gate`, `trigger_first_run`, `explain_run`) to dispatch a separate Cloud Tasks job. It sends a start email before dispatch, and a completion email after a completed, blocked or failed result. The start event retains the exact task ID in bounded thread context, allowing a later “status?” reply to call `check_workflow_status` without starting or explaining another run.

Each executing agent-team run maintains a durable trace in its isolated project database. The trace records timestamped phase transitions, safe diagnostics, evidence and artifact counts, checker state, delivery state, and terminal errors; it never copies source text, credentials, or full prompts into status mail. A normal status request reports queued, running, blocked, failed, or completed state and the current phase. Requests for verbose logs or tracing return the accumulated event stream. Before the project run is claimed, the Firestore mail job supplies authoritative queued/running state; after claim, the account-scoped project endpoint supplies live trace details. Task lookup verifies the stored tenant, project ID, and task ID before accessing the project endpoint.

The same coordinator exposes `check_ingestion_status` and `retry_ingestion` as tool calls. An ingestion job runs Drive and Gmail sequentially under the user's stored OAuth credential, writes per-provider progress records, and monitors the Cloud Run executions through durable task retries. The ready email is sent only when both providers succeed and at least one source was ingested or was already current under the graph extraction pipeline. Partial, empty, failed and uncertain launches never claim the graph is ready. Retries reuse completed item markers and cannot overlap an active run.

Project questions use the `answer_project_question` tool and a separate project-local answer agent. Supported answers include exact source quotations; unsupported questions report missing evidence. `get_project_graph_link` returns `/graphs/{project-id}`, while `get_workspace_graph_link` returns `/graphs/workspace` for the user's people, companies, funds and sources. These URLs contain no bearer token or tenant identifier. The frontend resolves them through the viewer's signed-in Google session, so forwarding an email cannot grant access to the sender's graph.

The email coordinator does not run project tools directly. Its worker task calls the ingestion service's account-scoped `/projects/{id}/agents/qc` or `/projects/{id}/agents/first-run` endpoint. Those teams have separate prompts and responsibilities:

* QC: an input-planning agent selects existing project artifacts and ratifications; the deterministic loader/terms checker runs; a review agent explains the results. Missing or ambiguous inputs block the run.
* First run: a producer attempts value-only workbook sheets and cited delivery rules; an independent reviewer checks them against project evidence. Outputs are retained as project artifacts. Missing inputs, review issues and context truncation are explicit. Draft production does not approve legal terms or mark a deliverable release-ready.
First-run draft workbooks are also copied to the requesting account's own Google Drive, into a `Private markets drafts` folder, using that account's stored connector credential. Delivery needs the `drive.file` scope, which grants access only to files this application created — it can never read or modify anything already in the account. **Accounts connected before this change hold a read-only grant and must reconnect Google before delivery works**; until then the run still completes and the reply says why delivery did not. A Drive failure never fails a run: the draft is already durable as a project artifact and downloadable from the reply.

* Explain: reads back what earlier runs recorded — run history, `finding` nodes and deterministic `check_result` nodes — and explains them. It materializes nothing, runs no checker and produces no deliverable. Its own reply is stored as an `explanation` node, a deliberately separate kind: later explains receive the three most recent as labelled prior context to reuse or correct, but never read them back as project evidence, so derived commentary cannot launder itself into the record.

A run that fails partway still records what it determined: findings collected before the error, the partial artifacts already committed, a `failure` finding naming the error, and a run node marked failed. The emailed summary stays generic; the detail lives in the database for a later explain. Recording a partial result can never mask the original failure.

Both producing workflows write their outputs twice into the project database: as immutable content-addressed artifacts (bytes in `blob`, metadata in `artifact`) for download and provenance, and as queryable `finding` nodes carrying the rules, sheets, missing inputs and review issues keyed by run. The explain workflow reads the nodes, so later questions never require decoding an artifact blob. Finding keys are content-derived, so a replayed run rewrites the same records.

## Infrastructure and authorization

`infrastructure/mail.tf` provisions a private Cloud Run service, named Firestore database, Cloud Tasks queue, runtime/task identities, narrow database access, Secret Manager access and Vertex model permissions. The public frontend relays only `/api/agentmail/webhook` to the private mail service, preserving raw bytes and Svix headers. All other mail-service routes require Cloud Run IAM. Signup is called only after the frontend verifies Google OAuth identity.

The webhook accepts only authenticated `message.received` events for the configured inbox and previously registered senders. It ignores spam, unauthenticated mail and auto-replies. Recipients always come from the stored verified account, never from model output or Reply-To. The worker signs a short-lived graph assertion for that account. Project authorization is checked again against its tenant's graph.

Firestore stores accounts, bounded thread context, jobs, tool calls, tool results, notification checkpoints and workflow results. Task payloads contain only job IDs. Transactional leases prevent concurrent processing. Retries reuse provider idempotency keys for each email stage; completion-mail retries reuse the saved workflow result. Cloud Tasks retains tasks for its platform retention window, so provider outages can delay mail; this is not a guarantee that a recipient's mail server will deliver it. Repeated workflow-service failures produce a final failure/uncertainty email rather than a false success.

## Setup

1. Install `services/mail_agent/requirements.txt` in a virtual environment. Put the AgentMail key in the repository root `.env` as `AGENTMAIL_API_KEY=...`. `.env`, Terraform state and local configuration are excluded from Cloud Build uploads.
2. Bootstrap the resources with `mail_enabled=true` and `mail_image=null`. Review a saved Terraform plan before applying; existing installations may need the frontend service account imported and the project's existing namespace bootstrap applied. Never replace the database VM to initialize a namespace.
3. Register the inbox and webhook and write the secrets:

   ```sh
   python infrastructure/scripts/setup_agentmail.py \
     --project=private-markets-hack \
     --origin=https://frontend-gucopvqxoq-nw.a.run.app \
     --inbox-id=zeroadmin@agentmail.to
   ```

   This uses inbox-scoped webhook permissions, writes `agentmail-api-key` and `agentmail-webhook-secret` to Secret Manager, and writes non-secret `infrastructure/agentmail.auto.tfvars.json`. The key needs inbox read, message send/reply and inbox webhook create/read permissions. The script prints no credentials. Omit `--inbox-id` only with permission to create an inbox. Keep the generated configuration; disabling `mail_enabled` would plan removal of mail resources.
4. Build/test the images. `cloudbuild-mail.yaml` defaults to build-only; set `_DEPLOY=true` for later releases. `cloudbuild.yaml` supports `_DEPLOY=false` to prepare an ingestion image before the coordinated rollout. Frontend build-only is the default when `_SERVICES` is empty.
5. Set `mail_image` to the tested image digest and `frontend_public_origin` to the existing frontend origin. Apply reviewed infrastructure changes. Deploy the tested ingestion and frontend images together with graph identity configuration. Cloud Build owns subsequent image updates; Terraform owns runtime environment and IAM. If the frontend was provisioned outside Terraform, import it before a full application apply or preserve its existing OAuth settings during a targeted rollout.
6. Verify Cloud Run readiness, the authenticated internal graph call, signature rejection and a real signup/reply cycle. Do not create fake accounts or email unrelated recipients for smoke tests.

Cloud Run sends every model request through the private `model-gateway` service (`MODEL_GATEWAY_URL`). Only the gateway has Vertex model permissions; mail and ingestion identities can invoke the gateway but cannot bypass it. The gateway preserves a canonical, unchanged prompt body across retries so Vertex implicit prefix caching remains effective. It records cache-token counts without prompt content and uses an additive-increase/multiplicative-decrease concurrency window with jittered backoff for 429 and transient 5xx responses. `GEMINI_API_KEY` remains an optional local development fallback. Every model-backed role is configured for `gemini-3.1-pro-preview`; deterministic checkers, ingestion control and link generation do not invoke a model.

## Tests and limits

```sh
PYTHONPATH=services/mail_agent python -m unittest discover -s services/mail_agent/tests -v
PYTHONPATH=services/ingestion python -m unittest discover -s services/ingestion/tests -v
cd frontend && npm test && npm run build
```

The first-run producer is bounded to 120,000 source characters, 150 sources, 10 sheets and 2,000 rows per sheet. It does not claim complete coverage when evidence exceeds those bounds. Citations must match project-local source text. Numeric output still requires human review and a separate deterministic QC run. Inbound attachments are not imported by this mail service; the existing connected-source ingestion path supplies project evidence.

Provider references: [AgentMail webhook verification](https://docs.agentmail.to/webhook-verification), [AgentMail receiving messages](https://docs.agentmail.to/messages), [Cloud Tasks authenticated HTTP tasks](https://docs.cloud.google.com/tasks/docs/creating-http-target-tasks), [Vertex structured output](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/control-generated-output).
