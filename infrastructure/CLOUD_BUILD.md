# Application releases through Cloud Build

## Release configuration

The five application images use explicit `main`-branch Cloud Build triggers. Each watches its source directory and build configuration, runs tests, publishes an immutable image digest and updates only its configured service/jobs. Pull requests run the separate GitHub Actions checks.

| Component | Trigger | Configuration | Target |
|---|---|---|---|
| Ingestion | `ingestion-main` | `cloudbuild.yaml` | `document-ingestion` |
| Frontend | `frontend-main` | `cloudbuild-frontend.yaml` | `frontend` |
| Google connectors | `connectors-main` | `cloudbuild-connectors.yaml` | Configured `connector-*` jobs |
| Mail agent | `mail-main` | `cloudbuild-mail.yaml` | `agent-mail` |
| Model gateway | `model-gateway-main` | `cloudbuild-model-gateway.yaml` | `model-gateway` |

The dedicated `ingestion-build` account receives image-repository writing, log writing, service/job-level Cloud Run Developer and permission to use the corresponding runtime accounts. It does not receive project-wide Editor or infrastructure-administration access.

A release rejects missing image digests and invalid targets. Ingestion, mail, gateway and connector pipelines allow explicitly requested `_DEPLOY=false` builds, with a build-only message. Their automatic triggers set `_DEPLOY=true`. Frontend releases require `_SERVICES=frontend`. Connector targets come from Terraform and must be nonempty for a release; updating their images never executes the jobs or starts ingestion. A failed job update stops the build; earlier job updates are not automatically rolled back.

## GitHub authorization modes

Set `enable_github_trigger = true` after connecting the repository to Cloud Build. Two modes are supported:

- `github_connection_mode = "github-app"` reuses the existing GitHub App repository authorization. These triggers live in `global`; their `_REGION` still selects the application region (`europe-west2`). No second-generation host connection is required.
- `github_connection_mode = "regional"` uses the authorized/imported second-generation `github` connection and a Terraform-managed repository in `var.region`. This remains the default for existing configurations that use second-generation connections.

Do not create both trigger sets for the same service. Existing GitHub App triggers can be imported into the matching Terraform trigger resource and updated in place, replacing autodetection with an explicit pipeline and source filter.

For `private-markets-hack`, the existing global trigger `4e2970a3-fe30-4891-9fc1-b70246d4823d` (`github-CD`) is imported as `google_cloudbuild_trigger.ingestion[0]`. Terraform renames it to `ingestion-main`; it is not left running as a duplicate.

## Existing application resources

Triggers are enabled only for configured application images/jobs. When a runtime is already deployed outside this state's application configuration, release-only settings avoid recreating or reconfiguring it:

```hcl
enable_github_trigger       = true
github_connection_mode      = "github-app"
frontend_existing_for_cd    = true
existing_connector_release_jobs = {
  connector-team-drive = "connector-team-drive@private-markets-hack.iam.gserviceaccount.com"
}
```

`frontend_existing_for_cd` expects the existing frontend runtime identity to be managed in this state. `existing_connector_release_jobs` maps existing job names to their runtime service-account emails. These settings manage only release targets and IAM bindings; the actual services, job schedules, environment variables and credentials are left alone. Do not also list the same job as both a managed connector and an existing release-only target.

The local `cd.auto.tfvars.json` holds the activation values and remains untracked, alongside the existing project configuration and Terraform state. Reproduce these values in the environment running Terraform before subsequent applies. Preserve the same state; otherwise existing triggers and IAM bindings must be imported before applying.

## Validation and rollout

Run `make test` and `make tf` before a release. HTTP/storage changes also require `make smoke`, and database changes require the live-database checks described in `AGENTS.md`. Infrastructure regression tests execute the actual release shell against a fake CLI and cannot deploy production resources.

Review the Terraform plan before applying configuration or IAM changes. Commit and merge the reviewed application/pipeline changes into `main`; Cloud Build owns image builds and production rollouts. Follow each triggered build through its deploy step and inspect the resulting ready revision before reporting a release as live. Do not bypass CI with workstation application deployments.

Terraform manages infrastructure, IAM, schedules and secrets separately from application releases. It ignores application image updates after provisioning so later infrastructure applies do not roll back a release. Database schema changes remain versioned migrations. Cross-service API changes must remain compatible during independent rollouts.
