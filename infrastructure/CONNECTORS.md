# Gmail and Google Drive connectors

`services/connectors` supplies a separate, lightweight Cloud Run Job worker. `connectors.tf` provisions one job, service identity, private bucket and OAuth secret per connection, plus optional Cloud Scheduler polling and a dedicated Cloud Build trigger. Defaults create no connectors and change no running services.

The path is **Gmail / Drive → private GCS originals / native-document exports + provenance → existing authenticated document ingestion → SurrealDB context**. Set `ingest = false` to archive without parsing. Each bucket is isolated to its worker; the existing parsed-document database/API is shared and is not a tenant-isolated application.

## Source coverage

- Gmail: traverses all pages with no date/count cap. The default includes Inbox, sent, archived, spam and trash. Set `query = "in:inbox"` for the entire Inbox folder only, or another Gmail search to narrow scope. Messages are preserved as raw EML including MIME attachments; the current parser processes email bodies/headers, not attachments as separate documents. Labels and thread IDs are retained in provenance at first capture.
- Drive: traverses all pages of accessible non-trashed files, across folders, without a file-type filter. Uploaded Word, Excel, PowerPoint, PDF, CSV, text, email, image and other binary files are downloaded unchanged. The default uses the user's corpus. Configure `drive_id` on a separate connection for each shared drive that needs guaranteed coverage. Incomplete search is an error. An optional Drive query narrows the scan; `"'FOLDER_ID' in parents"` selects direct children only, not a recursive folder tree. File shortcuts resolve to their accessible targets, preserving the link and deduplicating by target ID/revision. Folder shortcuts retain metadata only; they do not expand the scan's corpus. Google Photos libraries are separate.
- Drive revisions get separate archive/completion keys. Gmail contents use stable message IDs. Every run rescans the source and skips successfully completed items. This is polling, with per-item resume; it does not use Gmail history, Drive change cursors or push notifications. A live mailbox is not a transactional snapshot: later scans catch concurrent arrivals.
- Sources up to 256 MiB are archived; larger files fail explicitly. The worker reads `/formats` once per execution to discover the deployed parser's extensions and size limit (currently 20 MiB). Larger archived sources and formats unsupported by the parser are recorded as `archive_only`; they are not silently reported as parsed. Increase the worker's source limit and memory together if needed.

Native Google files are exported before archiving and parsing:

| Google source | Archived format | Processing |
| --- | --- | --- |
| Google Docs | DOCX | Document text and tables |
| Google Sheets | XLSX | Multiple worksheets; stored values, no formula recalculation |
| Google Slides | PPTX | Presentation text and tables |
| Google Drawings | PDF | PDF text / OCR |
| Apps Script | JSON | Archived; current parser does not support JSON |
| Other native types, such as Forms, Sites and Vids | Metadata only | Explicit `metadata_only` status with reason; no content archive claimed |

Google's `files.export` endpoint limits exports to 10 MB. Export failures (including size and permission failures) remain retryable and fail the execution after the rest of the scan completes. Exports represent the content available through the chosen format, not a complete backup of editor behavior, comments, revision history or app integrations. Original Google IDs, MIME types, names, parents and links are retained alongside the export MIME type. See [Google export formats](https://developers.google.com/workspace/drive/api/guides/ref-export-formats) and [download/export limits](https://developers.google.com/workspace/drive/api/guides/manage-downloads).

The same Drive read-only OAuth scope, job, bucket and IAM setup covers this expanded source coverage; no Docs/Sheets-specific API credentials are needed. Existing Drive connections automatically enumerate the broader source on their next execution after deploying the updated image. Remove any image-only custom query to include all files.

## 1. Bootstrap the connections

Add entries to private `infrastructure/terraform.tfvars`, leaving `connector_image` unset:

```hcl
google_connectors = {
  team-mail = { provider = "gmail" }
  team-drive = { provider = "drive" }
}
```

Use a distinct permanent key for each account/provider pair. Never rotate an existing connection's secret to a different account; create a new entry so identities and archives remain separate. Rotating credentials for the same account is supported.

```sh
terraform -chdir=infrastructure plan -out=connectors.tfplan
terraform -chdir=infrastructure apply connectors.tfplan
terraform -chdir=infrastructure output google_connectors
```

This enables the APIs and creates empty Secret Manager secrets and storage. Terraform does not create OAuth clients or hold account refresh tokens. The deployment identity needs permission to manage Storage and Cloud Scheduler in addition to the existing infrastructure permissions.

## 2. Authorize each account once

In the project's Google Auth Platform console, configure branding, audience and data access, then create a **Desktop app** OAuth client. Download its JSON to `.google-oauth/client.json`. The local setup tool uses a loopback browser callback; production jobs only use refresh tokens. It requests exactly `gmail.readonly` or `drive.readonly` for the chosen connector. GCP service-account IAM alone does not authorize access to a person's Gmail mailbox; this implementation uses user consent, not domain-wide delegation.

For an external app in Testing, add each account as a test user. These read-only scopes are restricted; public production use can require Google verification and a security assessment when storing restricted data on servers. Testing-mode refresh tokens for these scopes generally expire after seven days. Choose the appropriate internal/production audience before relying on unattended polling. See [Google OAuth token expiry](https://developers.google.com/identity/protocols/oauth2#expiration), [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes), and [Drive scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth).

From the repository root, with a local browser:

```sh
mkdir -p .google-oauth
chmod 700 .google-oauth
python3 -m venv .venv/connectors
.venv/connectors/bin/pip install -r services/connectors/requirements.txt
PYTHONPATH=services/connectors .venv/connectors/bin/python -m app.authorize gmail \
  --client .google-oauth/client.json --output .google-oauth/team-mail.json
PYTHONPATH=services/connectors .venv/connectors/bin/python -m app.authorize drive \
  --client .google-oauth/client.json --output .google-oauth/team-drive.json
gcloud secrets versions add connector-team-mail-oauth --project=PROJECT \
  --data-file=.google-oauth/team-mail.json
gcloud secrets versions add connector-team-drive-oauth --project=PROJECT \
  --data-file=.google-oauth/team-drive.json
```

The output files are created exclusively with mode `0600` and the directory is gitignored. Remove local token copies after uploading. A revoked/expired refresh token requires repeating consent and uploading a new secret version. Jobs use `latest` unless `secret_version` is pinned in the connection config. The account owner must complete consent; no account has been connected merely by provisioning resources.

## 3. Build, deploy and run

Build the initial worker image using Cloud Build. With `_JOBS` empty, this builds/pushes without updating jobs. Use your authorized build identity (the configured `ingestion-build` identity already has repository/log permissions):

```sh
gcloud builds submit . --project=PROJECT --region=europe-west2 \
  --service-account=projects/PROJECT/serviceAccounts/ingestion-build@PROJECT.iam.gserviceaccount.com \
  --config=cloudbuild-connectors.yaml
```

Set `connector_image` to the resulting `europe-west2-docker.pkg.dev/PROJECT/services/connectors@sha256:DIGEST`. Deploy ingestion first if `ingest = true` (the default), then plan/apply as in step 1. The job has one task, a 24-hour timeout, no automatic task retries, and no public endpoint. Runtime credentials come from Secret Manager; GCS access and authenticated ingestion calls use the job service account without downloaded service-account keys.

```sh
gcloud run jobs execute connector-team-mail --project=PROJECT --region=europe-west2
gcloud run jobs execute connector-team-drive --project=PROJECT --region=europe-west2
gcloud run jobs executions list --job=connector-team-mail --project=PROJECT --region=europe-west2
```

Operators need `roles/run.invoker` on the jobs to execute them and appropriate read/log permissions to inspect results. The scheduler has only job invocation permissions. Add `schedule = "0 */6 * * *"` to a connection and apply to poll every six hours UTC. Cloud Scheduler uses an OAuth token for the Cloud Run Admin API; it acknowledges job launch, not ingestion completion.

When the existing GitHub connection/trigger is enabled, Terraform adds `connectors-main`, watching the connector source and build YAML. It tests, builds and updates the explicitly configured jobs with the image digest. Terraform owns job configuration and ignores subsequent image updates, matching the ingestion deployment pattern. This pipeline does not execute imports on deployment.

## Progress, retries and retention

Each bucket contains `raw/<key>`, `metadata/<key>.json`, `completed/<key>.json`, `state/lease.json` and `state/last_run.json`. Completion records link source IDs/revisions to parsed document IDs, or give the archive-only / metadata-only reason. Shortcut metadata links to the target object key. A metadata-only record has no raw object. Counts and hashed failing item keys are logged without subjects, filenames, message content or credentials.

API reads/downloads retry transient errors up to five times with the Google client's backoff. Item errors allow the scan to continue, leave no completion record and make the execution fail at the end. Rerun manually or wait for the next schedule to retry unfinished items. Successful records are skipped, including after a job timeout. The parser's content-hash upsert makes a crash after parsing but before completion safe to replay. Full rescans still incur listing and GCS marker-read costs proportional to source size.

A GCS generation-guarded lease prevents overlapping executions. It renews during processing and expires after 30 minutes if a worker is killed; an overlapping execution exits without doing work. Do not manually delete a lease while an execution is active. `state/last_run.json` describes the last scan that finished enumeration, not a timed-out scan. Monitor Cloud Run execution failures as well as counts; no alerting policy is included.

Archives are retained without an automatic expiry and buckets cannot be force-destroyed while nonempty. Source deletion/revocation does not delete previously archived or parsed material. Disconnect by removing the schedule, cancelling active executions, revoking consent and disabling the secret; arrange archive/database deletion separately if required. To retry an `archive_only` or `metadata_only` item after expanding parser support, remove its completion marker. To reparse all completed items after a parser upgrade, clear completion markers while the connector is stopped; preserve raw files and metadata.

## Verification

```sh
PYTHONPATH=services/connectors .venv/connectors/bin/python -m unittest discover -s services/connectors/tests -v
terraform -chdir=infrastructure validate
```

Unit tests cover Office/native-document exports, binary preservation, parser format discovery, shortcuts, unsupported native types, export failures, pagination, shared-drive scoping, incomplete-search detection, Drive revisions, raw email decoding, idempotency, retries after ingestion failures, archive-to-parser transitions, size limits and lease contention. Before scheduling real accounts, run a small consented source query, verify originals/provenance and document IDs, then rerun and check `unchanged` counts. Remove the query to start the complete backfill.

API references: [Gmail message listing](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list), [Drive file listing](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list), [installed-app OAuth](https://developers.google.com/identity/protocols/oauth2/native-app).
