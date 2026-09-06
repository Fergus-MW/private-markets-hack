# Application releases through Cloud Build

## Current status

The pipeline is published on `main` and a real Cloud Build run successfully tested, built, pushed, and deployed the ingestion service: [verified build](https://console.cloud.google.com/cloud-build/builds;region=europe-west2/33083c57-7b88-4f35-a79a-1bccad8a90ca?project=230147580347).

**Automatic push triggering is not active yet.** Read-only inspection on 6 September 2026 found no Cloud Build GitHub connection or regional triggers. Authorize the GitHub App connection, import it into Terraform, then enable and apply the repository/trigger resources below. A pipeline file alone does not activate releases.

## Pipeline

`cloudbuild.yaml` tests, builds, publishes, and deploys the ingestion service on pushes to `main` that change `services/ingestion/**` or `cloudbuild.yaml`. Pull requests do not deploy. Existing GitHub Actions checks can continue to run independently.

Cloud Build uses `ingestion-build@private-markets-hack.iam.gserviceaccount.com`, with repository-level image publishing, service-level Cloud Run developer access, permission to act as the ingestion runtime account, and log writing. It has no database secret access or Terraform privileges. Each build uses a unique image tag and deploys the immutable digest. Failed tests or image builds stop deployment; Cloud Run waits for the updated service to become ready.

Terraform manages infrastructure and IAM; Cloud Build manages the deployed container image. Terraform deliberately ignores subsequent image changes so an infrastructure apply does not roll back an application deployment. Application rollouts, including rollbacks, must run through CI/CD; do not submit builds or update application images from a workstation.

## CD coverage

All five application images have CI tests and a Cloud Build release path. The triggers below are created only after `enable_github_trigger = true` and the corresponding service/job is configured in Terraform. All watch `main` only, and filter on the service source directory and its Cloud Build YAML.

| Component | Trigger | Release target | Provisioning condition |
|---|---|---|---|
| Frontend | `frontend-main` | Cloud Run `frontend` | `frontend_image` configured |
| Ingestion | `ingestion-main` | Cloud Run `document-ingestion` | `ingestion_image` configured |
| Google connectors | `connectors-main` | Each configured `connector-*` job | `connector_image` and jobs configured |
| Mail agent | `mail-main` | Cloud Run `agent-mail` | `mail_enabled` and `mail_image` configured |
| Model gateway | `model-gateway-main` | Cloud Run `model-gateway` | `model_gateway_image` configured |

The mail and gateway triggers explicitly set `_DEPLOY=true`; gateway releases now include a deploy step. Ingestion also receives its configured region and an explicit deploy flag. Build-only runs for these services and connectors require `_DEPLOY=false` and print that deployment was disabled. Invalid deploy flags fail. Connector releases derive `_JOBS` from Terraform's configured jobs and reject empty or invalid targets. Updating a connector image does not execute the jobs or start data ingestion; it takes effect on subsequent scheduled/requested executions. If one job update fails, the build fails and stops; earlier successful job updates are not rolled back automatically.

Application builds do not apply infrastructure changes. VM configuration, IAM, secrets, schedules and Terraform state still require a reviewed Terraform plan/apply. Database schema changes remain versioned migrations, with local live-database tests; application release automation does not replace those checks. Cross-service API changes must remain compatible during independent rollouts.

Local regression tests execute the actual deployment shell against a fake CLI, checking target validation, explicit build-only behavior, immutable digests and failure propagation. These tests do not access Cloud Run. The activation blocker is shared by every service: the GitHub connection and triggers are not live yet.

## GitHub connection

The Terraform-managed `github` connection requires a one-time browser authorization with GitHub. The provider omits an empty GitHub configuration on initial creation, so bootstrap the connection using `gcloud builds connections create github github --project=private-markets-hack --region=europe-west2`, then import it with `terraform -chdir=infrastructure import 'google_cloudbuildv2_connection.github[0]' projects/private-markets-hack/locations/europe-west2/connections/github` (the connection is gated behind `enable_github_trigger`, so it is a counted resource). Authorize the Google Cloud Build GitHub App for `Fergus-MW/private-markets-hack`. No GitHub personal access token is stored in the repo.

```sh
gcloud builds connections describe github --project=private-markets-hack --region=europe-west2
```

Connection setup also requires the Cloud Build service agent to have `secretmanager.secrets.create` and `secretmanager.secrets.setIamPolicy`; the custom role and binding are defined in `cloudbuild.tf`. These project-level permissions require explicit approval before applying them.

Follow `installationState.actionUri` until the state is `COMPLETE`. Set `enable_github_trigger = true` in the local `infrastructure/terraform.tfvars`, then plan and apply Terraform to register the repository and create the configured application triggers listed above. The pipeline file must be pushed to `main` before using the trigger.

Infrastructure changes still require a separate Terraform plan/apply with the existing state. The build does not read `.tfvars`, Terraform state, or local credentials.

## Frontend releases

When `enable_github_trigger = true` and `frontend_image` is configured, Terraform creates `frontend-main`. It watches pushes to `main` changing `frontend/**` or `cloudbuild-frontend.yaml`; pull requests continue to run the existing GitHub Actions checks without deploying.

The frontend pipeline runs its tests and production build, builds and publishes the container, resolves its immutable digest, and waits for the Cloud Run update. `_SERVICES` defaults to `frontend` and the trigger sets it explicitly. An empty or unexpected target fails the build instead of silently skipping deployment. The build identity gets Cloud Run Developer on the frontend service and Service Account User on its runtime identity, with existing log and image-repository access.

Activation requires the authorized GitHub connection above plus the existing frontend service in Terraform state. If the service was provisioned outside this state, import it and its existing identity/configuration before applying; do not recreate it. Set `frontend_image` to the deployed digest and configure the existing public origin. Review the Terraform plan, then apply it to install the repository, triggers and scoped IAM bindings.

Commit and push reviewed changes. Once a frontend commit reaches `main`, follow the `frontend-main` build through its deploy step; a successful push or image build alone is not a completed release. Confirm the resulting ready revision and traffic before reporting it live. There is no automatic release until the connection and trigger are active.

## Knowledge graph and multi-user rollout

`workflows.tf` now owns workflow and graph identity secrets, their service access,
plus `terraform_data.project_namespace`. The latter runs the checked-in migration
through IAP before ingestion is updated. It bootstraps existing VMs without a
startup-script rerun or replacement. Apply requires Python 3, gcloud and the
operator's existing IAP/OS Login access. No database password is passed on the
command line or fetched onto the operator's machine.

After reviewing and applying the Terraform plan, release all three updated
images through CI/CD: `cloudbuild.yaml`, `cloudbuild-connectors.yaml` (set `_JOBS` to the
configured jobs), and `cloudbuild-frontend.yaml` (set `_SERVICES=frontend`). A Terraform apply alone does not
build application code. See `services/ingestion/MULTI_USER.md` for identity flow,
legacy-data ownership and rollout details. Production bootstrap and build upload
have not been executed as part of this code update.
