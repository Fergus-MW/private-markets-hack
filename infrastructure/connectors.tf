# Bootstrap secrets/buckets first, then set connector_image after account consent.
variable "connector_image" {
  description = "Connector worker image URI, preferably pinned by digest. Null provisions only storage, identities and empty secrets."
  type        = string
  default     = null
}

variable "google_connectors" {
  description = "One entry per account/source. Keys must be short, non-sensitive slugs. Schedules are opt-in (UTC)."
  type = map(object({
    provider       = string
    query          = optional(string, "")
    drive_id       = optional(string, "")
    schedule       = optional(string)
    ingest         = optional(bool, true)
    secret_version = optional(string, "latest")
  }))
  default = {}
  validation {
    condition = alltrue([for key, config in var.google_connectors :
      can(regex("^[a-z][a-z0-9-]{0,18}[a-z0-9]$", key)) && contains(["gmail", "drive"], config.provider)
    ])
    error_message = "Connector keys must be 2–20 lowercase letters/digits/hyphens, starting with a letter and ending with a letter/digit; provider must be gmail or drive."
  }
}

locals {
  connector_jobs = var.connector_image == null ? {} : var.google_connectors
}

resource "google_project_service" "connectors" {
  for_each           = length(var.google_connectors) == 0 ? toset([]) : toset(["gmail.googleapis.com", "drive.googleapis.com", "storage.googleapis.com", "cloudscheduler.googleapis.com"])
  project            = google_project.main.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "connector" {
  for_each     = var.google_connectors
  account_id   = "connector-${each.key}"
  display_name = "Google source connector ${each.key}"
  depends_on   = [google_project_service.apis]
}

resource "google_secret_manager_secret" "connector" {
  for_each  = var.google_connectors
  secret_id = "connector-${each.key}-oauth"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# Credentials are uploaded outside Terraform so refresh tokens never enter state.
resource "google_secret_manager_secret_iam_member" "connector" {
  for_each  = var.google_connectors
  secret_id = google_secret_manager_secret.connector[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.connector[each.key].email}"
}

resource "google_storage_bucket" "connector" {
  for_each                    = var.google_connectors
  name                        = "${var.project_id}-connector-${each.key}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  depends_on                  = [google_project_service.connectors]
}

resource "google_storage_bucket_iam_member" "connector" {
  for_each = var.google_connectors
  bucket   = google_storage_bucket.connector[each.key].name
  role     = "roles/storage.objectUser"
  member   = "serviceAccount:${google_service_account.connector[each.key].email}"
}

resource "google_cloud_run_v2_service_iam_member" "connector_ingest" {
  for_each = var.ingestion_image == null ? {} : { for key, config in var.google_connectors : key => config if config.ingest }
  name     = google_cloud_run_v2_service.ingestion[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.connector[each.key].email}"
}

resource "google_cloud_run_v2_job" "connector" {
  for_each            = local.connector_jobs
  name                = "connector-${each.key}"
  location            = var.region
  deletion_protection = false
  lifecycle {
    # Dedicated Cloud Build pipeline owns subsequent image updates.
    ignore_changes = [template[0].template[0].containers[0].image]
    precondition {
      condition     = !each.value.ingest || var.ingestion_image != null
      error_message = "Deploy ingestion first, or set ingest = false for an archive-only connection."
    }
  }
  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = google_service_account.connector[each.key].email
      timeout         = "86400s"
      max_retries     = 0
      containers {
        image = var.connector_image
        resources {
          limits = { cpu = "1", memory = "2Gi" }
        }
        dynamic "env" {
          for_each = {
            CONNECTOR_PROVIDER = each.value.provider
            CONNECTOR_BUCKET   = google_storage_bucket.connector[each.key].name
            SOURCE_QUERY       = each.value.query
            DRIVE_ID           = each.value.drive_id
            INGESTION_URL      = each.value.ingest ? try(google_cloud_run_v2_service.ingestion[0].uri, "") : ""
            # Named, not mounted: a mounted secret must already hold a version at
            # deploy time, and execution overrides cannot repoint a secret ref.
            # The worker reads this at run time, so one job serves many accounts.
            CONNECTOR_SECRET = "${google_secret_manager_secret.connector[each.key].id}/versions/${each.value.secret_version}"
          }
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.connector,
  google_storage_bucket_iam_member.connector, google_cloud_run_v2_service_iam_member.connector_ingest]
}

resource "google_service_account" "connector_scheduler" {
  count      = length(var.google_connectors) == 0 ? 0 : 1
  account_id = "connector-scheduler"
  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_job_iam_member" "connector_scheduler" {
  for_each = local.connector_jobs
  name     = google_cloud_run_v2_job.connector[each.key].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.connector_scheduler[0].email}"
}

resource "google_cloud_scheduler_job" "connector" {
  for_each  = { for key, config in local.connector_jobs : key => config if config.schedule != null }
  name      = "connector-${each.key}"
  region    = var.region
  schedule  = each.value.schedule
  time_zone = "Etc/UTC"
  http_target {
    uri         = "https://run.googleapis.com/v2/${google_cloud_run_v2_job.connector[each.key].id}:run"
    http_method = "POST"
    body        = base64encode("{}")
    headers     = { "Content-Type" = "application/json" }
    oauth_token {
      service_account_email = google_service_account.connector_scheduler[0].email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
  depends_on = [google_project_service.connectors, google_cloud_run_v2_job_iam_member.connector_scheduler]
}

resource "google_cloud_run_v2_job_iam_member" "connector_build" {
  for_each = local.connector_jobs
  name     = google_cloud_run_v2_job.connector[each.key].name
  location = var.region
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.build.email}"
}

resource "google_service_account_iam_member" "connector_build" {
  for_each           = var.google_connectors
  service_account_id = google_service_account.connector[each.key].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.build.email}"
}

resource "google_cloudbuild_trigger" "connectors" {
  count           = var.enable_github_trigger && length(local.connector_jobs) > 0 ? 1 : 0
  name            = "connectors-main"
  location        = var.region
  service_account = google_service_account.build.id
  filename        = "cloudbuild-connectors.yaml"
  included_files  = ["services/connectors/**", "cloudbuild-connectors.yaml"]
  substitutions = {
    _REGION = var.region
    _JOBS   = join(" ", [for key in sort(keys(local.connector_jobs)) : "connector-${key}"])
  }
  repository_event_config {
    repository = google_cloudbuildv2_repository.main[0].id
    push { branch = "^main$" }
  }
  depends_on = [google_cloud_run_v2_job_iam_member.connector_build, google_service_account_iam_member.connector_build,
  google_project_iam_member.build_logs, google_artifact_registry_repository_iam_member.build_push]
}

output "google_connectors" {
  value = { for key, config in var.google_connectors : key => {
    bucket = google_storage_bucket.connector[key].name
    secret = google_secret_manager_secret.connector[key].secret_id
    job    = try(google_cloud_run_v2_job.connector[key].name, null)
  } }
}
