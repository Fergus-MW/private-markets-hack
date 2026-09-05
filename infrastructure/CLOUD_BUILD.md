# Automatic ingestion deployments

## Current status

The pipeline is published on `main` and a real Cloud Build run successfully tested, built, pushed, and deployed the ingestion service: [verified build](https://console.cloud.google.com/cloud-build/builds;region=europe-west2/33083c57-7b88-4f35-a79a-1bccad8a90ca?project=230147580347).

**Automatic push triggering is not active yet.** The GitHub connection is awaiting approval for its service-agent Secret Manager setup permissions, followed by GitHub App browser authorization. After that, enable and apply the repository/trigger resources as described below.

## Pipeline

`cloudbuild.yaml` tests, builds, publishes, and deploys the ingestion service on pushes to `main` that change `services/ingestion/**` or `cloudbuild.yaml`. Pull requests do not deploy. Existing GitHub Actions checks can continue to run independently.

Cloud Build uses `ingestion-build@private-markets-hack.iam.gserviceaccount.com`, with repository-level image publishing, service-level Cloud Run developer access, permission to act as the ingestion runtime account, and log writing. It has no database secret access or Terraform privileges. Each build uses a unique image tag and deploys the immutable digest. Failed tests or image builds stop deployment; Cloud Run waits for the updated service to become ready.

Terraform manages infrastructure and IAM; Cloud Build manages the deployed container image. Terraform deliberately ignores subsequent image changes so an infrastructure apply does not roll back an application deployment. To roll back an application, deploy a previously published image digest with `gcloud run services update document-ingestion --project=private-markets-hack --region=europe-west2 --image=IMAGE_DIGEST`.

## GitHub connection

The Terraform-managed `github` connection requires a one-time browser authorization with GitHub. The provider omits an empty GitHub configuration on initial creation, so bootstrap the connection using `gcloud builds connections create github github --project=private-markets-hack --region=europe-west2`, then import it with `terraform -chdir=infrastructure import 'google_cloudbuildv2_connection.github[0]' projects/private-markets-hack/locations/europe-west2/connections/github` (the connection is gated behind `enable_github_trigger`, so it is a counted resource). Authorize the Google Cloud Build GitHub App for `Fergus-MW/private-markets-hack`. No GitHub personal access token is stored in the repo.

```sh
gcloud builds connections describe github --project=private-markets-hack --region=europe-west2
```

Connection setup also requires the Cloud Build service agent to have `secretmanager.secrets.create` and `secretmanager.secrets.setIamPolicy`; the custom role and binding are defined in `cloudbuild.tf`. These project-level permissions require explicit approval before applying them.

Follow `installationState.actionUri` until the state is `COMPLETE`. Set `enable_github_trigger = true` in the local `infrastructure/terraform.tfvars`, then plan and apply Terraform to register the repository and create the `ingestion-main` trigger. The pipeline file must be pushed to `main` before using the trigger.

Infrastructure changes still require a separate Terraform plan/apply with the existing state. The build does not read `.tfvars`, Terraform state, or local credentials.

Manual build from the public repo, useful for verifying deployment before completing the GitHub App connection:

```sh
gcloud builds submit https://github.com/Fergus-MW/private-markets-hack.git \
  --git-source-revision=main --config=cloudbuild.yaml \
  --project=private-markets-hack --region=europe-west2 \
  --service-account=projects/private-markets-hack/serviceAccounts/ingestion-build@private-markets-hack.iam.gserviceaccount.com
```
