# GCP deployment

This configuration creates a new project named **private markets hack** under the supplied billing account. Organisation placement is optional; the current deployment uses no organisation parent, matching the supplied `grenertia` project. It does not infer IDs from a company name. No cloud resources have been created until `terraform apply` completes.

## First deployment

Authenticate with an account authorized to create projects and attach billing:

```sh
gcloud auth login
gcloud auth application-default login
gcloud organizations list
gcloud billing accounts list
cp infrastructure/terraform.tfvars.example infrastructure/terraform.tfvars
```

Fill in the billing account, optional organisation ID, a globally unique lowercase project ID, and intended IAM invokers. The deploying identity needs project creation and billing attachment rights plus permission to manage the project's APIs, IAM, Compute, Cloud Run, Artifact Registry, and Secret Manager resources. Adjust both region and zone together if London is unsuitable.

```sh
terraform -chdir=infrastructure init
terraform -chdir=infrastructure plan -out=bootstrap.tfplan
terraform -chdir=infrastructure apply bootstrap.tfplan
```

Initially leave `ingestion_image` unset: Terraform creates the image repository and database first. Build an AMD64 service image for Cloud Run (including when building on an Apple Silicon Mac):

```sh
gcloud auth configure-docker europe-west2-docker.pkg.dev
# Replace PROJECT with the project_id from terraform.tfvars.
docker buildx build --platform linux/amd64 --push \
  -t europe-west2-docker.pkg.dev/PROJECT/services/ingestion:v1 services/ingestion
```

Set `ingestion_image` in `terraform.tfvars` to the pushed image URI, preferably using the SHA-256 digest printed by the build. Then deploy the service:

```sh
terraform -chdir=infrastructure plan -out=service.tfplan
terraform -chdir=infrastructure apply service.tfplan
terraform -chdir=infrastructure output ingestion_url
```

Using the returned URL:

```sh
curl -f -H "Authorization: Bearer $(gcloud auth print-identity-token)" https://SERVICE_URL/readyz
curl -f -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -F 'file=@report.pdf' https://SERVICE_URL/documents
```

Unauthenticated calls must return 403. A successful Terraform apply does not prove VM bootstrap succeeded: check `/readyz` and ingest a fixture. Inspect VM startup logs through IAP/OS Login if readiness fails. The operator needs IAP tunnel and OS Login permissions; Terraform does not grant these broadly.

## State and secrets

Random passwords are stored in Secret Manager and also in Terraform state. State, plans, and `.tfvars` are gitignored. Keep local state private and backed up. Before shared/team operation, configure an access-controlled GCS state bucket with versioning and migrate using `terraform init -migrate-state`; do not share plaintext state or commit it. The provider lock file is included for reproducible provider selection.

The VM reads secrets through its service identity and bootstraps a database-scoped editor. Cloud Run can read only the ingestion password. Password rotation needs a coordinated database-user update and Cloud Run revision; changing Secret Manager alone does not change SurrealDB's persisted root password. Never replace the random root password resource without a corresponding database password rotation.

## Operations and recovery

Data disk destruction is blocked by Terraform lifecycle protection; VM deletion and project deletion are protected as well. This deliberately requires explicit configuration changes for teardown. Daily snapshots are crash-consistent disk snapshots, not application-level exports. Test recovery before relying on backups for production.

Recovery: stop the VM, restore a selected snapshot to a new disk in the same zone, attach it as `surrealdb-data`, reconcile/import the restored disk into Terraform, and restart. Preserve the old disk until validation succeeds. The startup script mounts existing filesystems and formats only a blank disk. Docker is ordered after the persistent mount on reboot. Check `/readyz` and compare document/mention counts after restore.

The small VM has 2 GiB RAM and a 1.5 GiB database container limit. This is a starting size for modest ingestion, not a demonstrated capacity guarantee. Watch disk use, memory, latency, and snapshot growth. Increase capacity before the 10 GB data disk fills. No automatic resizing, autoscaling database cluster, or alerting policy is included.
