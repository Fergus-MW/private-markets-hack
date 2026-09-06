variable "mail_enabled" {
  description = "Provision durable email-agent infrastructure. Requires frontend and ingestion services."
  type        = bool
  default     = false
}
variable "mail_image" {
  description = "Email-agent image URI. Null provisions infrastructure and secrets before deployment."
  type        = string
  default     = null
}
variable "agentmail_inbox_id" {
  description = "AgentMail sender inbox created by setup_agentmail.py."
  type        = string
  default     = ""
}
variable "mail_gemini_model" {
  type    = string
  default = "gemini-3.1-pro-preview"
}
variable "mail_existing_connector_accounts" {
  description = "Existing connector job identities that must sign ingestion requests during the mail rollout."
  type        = set(string)
  default     = []
}
resource "google_secret_manager_secret_iam_member" "mail_existing_connector_identity" {
  for_each  = var.mail_enabled ? var.mail_existing_connector_accounts : toset([])
  secret_id = google_secret_manager_secret.graph_identity.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}

locals {
  mail_url = "https://agent-mail-${google_project.main.number}.${var.region}.run.app"
}

resource "google_project_service" "mail" {
  for_each           = var.mail_enabled ? toset(["cloudtasks.googleapis.com", "firestore.googleapis.com", "aiplatform.googleapis.com"]) : toset([])
  project            = google_project.main.project_id
  service            = each.value
  disable_on_destroy = false
}
resource "google_service_account" "mail" {
  count      = var.mail_enabled ? 1 : 0
  account_id = "agent-mail"
  depends_on = [google_project_service.apis]
}
resource "google_service_account" "mail_task" {
  count      = var.mail_enabled ? 1 : 0
  account_id = "agent-mail-tasks"
  depends_on = [google_project_service.apis]
}
resource "google_firestore_database" "mail" {
  count                   = var.mail_enabled ? 1 : 0
  project                 = google_project.main.project_id
  name                    = "agent-mail"
  location_id             = var.region
  type                    = "FIRESTORE_NATIVE"
  delete_protection_state = "DELETE_PROTECTION_ENABLED"
  deletion_policy         = "ABANDON"
  depends_on              = [google_project_service.mail]
}
resource "google_project_iam_member" "mail_database" {
  count   = var.mail_enabled ? 1 : 0
  project = google_project.main.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.mail[0].email}"
  condition {
    title      = "Email agent database only"
    expression = "resource.name == 'projects/${google_project.main.project_id}/databases/${google_firestore_database.mail[0].name}'"
  }
}
resource "google_cloud_tasks_queue" "mail" {
  count    = var.mail_enabled ? 1 : 0
  name     = "agent-mail"
  project  = google_project.main.project_id
  location = var.region
  rate_limits {
    max_concurrent_dispatches = 4
    max_dispatches_per_second = 2
  }
  retry_config {
    max_attempts       = -1
    max_retry_duration = "0s"
    min_backoff        = "30s"
    max_backoff        = "1800s"
    max_doublings      = 6
  }
  stackdriver_logging_config {
    sampling_ratio = 1
  }
  depends_on = [google_project_service.mail]
}
resource "google_cloud_tasks_queue_iam_member" "mail_enqueue" {
  count    = var.mail_enabled ? 1 : 0
  project  = google_project.main.project_id
  location = var.region
  name     = google_cloud_tasks_queue.mail[0].name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.mail[0].email}"
}
resource "google_service_account_iam_member" "mail_act_as_task" {
  count              = var.mail_enabled ? 1 : 0
  service_account_id = google_service_account.mail_task[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.mail[0].email}"
}
resource "google_secret_manager_secret" "mail" {
  for_each  = var.mail_enabled ? toset(["api-key", "webhook-secret"]) : toset([])
  secret_id = "agentmail-${each.key}"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}
resource "google_secret_manager_secret_iam_member" "mail" {
  for_each  = google_secret_manager_secret.mail
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.mail[0].email}"
}
resource "google_secret_manager_secret_iam_member" "mail_identity" {
  count     = var.mail_enabled ? 1 : 0
  secret_id = google_secret_manager_secret.graph_identity.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.mail[0].email}"
}
resource "google_project_iam_member" "mail_models" {
  for_each = var.model_gateway_image == null ? {} : {
    gateway = google_service_account.model_gateway[0].email
  }
  project    = google_project.main.project_id
  role       = "roles/aiplatform.user"
  member     = "serviceAccount:${each.value}"
  depends_on = [google_project_service.mail]
}
resource "google_cloud_run_v2_service_iam_member" "mail_graph" {
  count    = var.mail_enabled && var.ingestion_image != null ? 1 : 0
  name     = google_cloud_run_v2_service.ingestion[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.mail[0].email}"
}
resource "google_cloud_run_v2_service" "mail" {
  count               = var.mail_enabled && var.mail_image != null ? 1 : 0
  name                = "agent-mail"
  project             = google_project.main.project_id
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
    precondition {
      condition     = var.frontend_public_origin != null && var.ingestion_image != null && var.agentmail_inbox_id != ""
      error_message = "Mail requires the frontend origin, ingestion service and an AgentMail inbox."
    }
  }
  template {
    service_account                  = google_service_account.mail[0].email
    timeout                          = "1800s"
    max_instance_request_concurrency = 4
    scaling {
      max_instance_count = 3
    }
    containers {
      image = var.mail_image
      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }
      dynamic "env" {
        for_each = {
          INGESTION_CONNECTOR_JOB       = "projects/${google_project.main.project_id}/locations/${var.region}/jobs/${var.mail_connector_job}"
          INGESTION_STATUS_BUCKET       = var.mail_ingestion_status_bucket
          MAIL_DATABASE                 = google_firestore_database.mail[0].name
          MAIL_QUEUE                    = google_cloud_tasks_queue.mail[0].id
          MAIL_SERVICE_URL              = local.mail_url
          MAIL_TASK_ACCOUNT             = google_service_account.mail_task[0].email
          AGENTMAIL_INBOX_ID            = var.agentmail_inbox_id
          INGESTION_URL                 = google_cloud_run_v2_service.ingestion[0].uri
          FRONTEND_PUBLIC_ORIGIN        = var.frontend_public_origin
          GEMINI_MODEL                  = var.mail_gemini_model
          MODEL_GATEWAY_URL             = google_cloud_run_v2_service.model_gateway[0].uri
          GOOGLE_CLOUD_PROJECT          = google_project.main.project_id
          INGESTION_POLL_WINDOW_SECONDS = tostring(var.mail_poll_window_seconds)
        }
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = {
          AGENTMAIL_API_KEY        = google_secret_manager_secret.mail["api-key"].secret_id
          AGENTMAIL_WEBHOOK_SECRET = google_secret_manager_secret.mail["webhook-secret"].secret_id
          GRAPH_IDENTITY_SECRET    = google_secret_manager_secret.graph_identity.secret_id
        }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
    }
  }
  depends_on = [google_project_iam_member.mail_models, google_secret_manager_secret_iam_member.mail, google_secret_manager_secret_iam_member.mail_identity,
    google_project_iam_member.mail_database, google_cloud_tasks_queue_iam_member.mail_enqueue,
    google_service_account_iam_member.mail_act_as_task, google_cloud_run_v2_service_iam_member.mail_graph,
  google_cloud_run_v2_service_iam_member.model_gateway_invoker]
}
resource "google_cloud_run_v2_service_iam_member" "mail_invoker" {
  for_each = var.mail_enabled && var.mail_image != null ? {
    frontend = google_service_account.frontend[0].email
    worker   = google_service_account.mail_task[0].email
  } : {}
  name     = google_cloud_run_v2_service.mail[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${each.value}"
}
resource "google_service_account_iam_member" "mail_build" {
  count              = var.mail_enabled ? 1 : 0
  service_account_id = google_service_account.mail[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.build.email}"
}
resource "google_cloud_run_v2_service_iam_member" "mail_build" {
  # Release IAM targets a provisioned service; it must not pull runtime drift into a CD apply.
  count    = var.mail_enabled && var.mail_image != null ? 1 : 0
  name     = "agent-mail"
  location = var.region
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.build.email}"
}
output "mail_service_url" {
  value = try(google_cloud_run_v2_service.mail[0].uri, null)
}

resource "google_cloudbuild_trigger" "mail" {
  count           = var.enable_github_trigger && var.mail_enabled && var.mail_image != null ? 1 : 0
  name            = "mail-main"
  location        = local.github_trigger_location
  service_account = google_service_account.build.id
  filename        = "cloudbuild-mail.yaml"
  included_files  = ["services/mail_agent/**", "cloudbuild-mail.yaml"]
  substitutions = {
    _REGION = var.region
    _DEPLOY = "true"
  }
  dynamic "github" {
    for_each = var.github_connection_mode == "github-app" ? [1] : []
    content {
      owner = "Fergus-MW"
      name  = "private-markets-hack"
      push { branch = "^main$" }
    }
  }
  dynamic "repository_event_config" {
    for_each = var.github_connection_mode == "regional" ? [1] : []
    content {
      repository = google_cloudbuildv2_repository.main[0].id
      push { branch = "^main$" }
    }
  }
  depends_on = [google_cloud_run_v2_service_iam_member.mail_build,
    google_service_account_iam_member.mail_build,
  google_project_iam_member.build_logs, google_artifact_registry_repository_iam_member.build_push]
}

variable "mail_connector_job" {
  description = "Existing Cloud Run connector job used for account-scoped ingestion executions."
  type        = string
  default     = "connector-team-drive"
}
variable "mail_ingestion_status_bucket" {
  description = "Connector archive bucket containing ingestion-status progress objects."
  type        = string
  default     = "private-markets-hack-connector-team-drive"
}
resource "google_project_iam_custom_role" "mail_ingestion" {
  count       = var.mail_enabled ? 1 : 0
  project     = google_project.main.project_id
  role_id     = "mailIngestionRunner"
  title       = "Email agent ingestion runner"
  permissions = ["run.jobs.run", "run.jobs.runWithOverrides", "run.executions.list"]
}
resource "google_cloud_run_v2_job_iam_member" "mail_ingestion" {
  count    = var.mail_enabled ? 1 : 0
  project  = google_project.main.project_id
  location = var.region
  name     = var.mail_connector_job
  role     = google_project_iam_custom_role.mail_ingestion[0].name
  member   = "serviceAccount:${google_service_account.mail[0].email}"
}
resource "google_storage_bucket_iam_member" "mail_ingestion_status" {
  count  = var.mail_enabled ? 1 : 0
  bucket = var.mail_ingestion_status_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.mail[0].email}"
  condition {
    title      = "Ingestion progress objects only"
    expression = "resource.name.startsWith('projects/_/buckets/${var.mail_ingestion_status_bucket}/objects/ingestion-status/')"
  }
}

# New mail in a user's own mailbox raises no event this system can receive, so
# ingestion is polled. The endpoint is a no-op for accounts whose previous run
# is still in flight, so the cadence only bounds how stale a graph can get.
resource "google_service_account" "mail_scheduler" {
  count        = var.mail_enabled && var.mail_image != null ? 1 : 0
  project      = google_project.main.project_id
  account_id   = "mail-scheduler"
  display_name = "Scheduled ingestion polling for the mail agent"
}

resource "google_cloud_run_v2_service_iam_member" "mail_scheduler" {
  count    = var.mail_enabled && var.mail_image != null ? 1 : 0
  project  = google_project.main.project_id
  location = var.region
  name     = google_cloud_run_v2_service.mail[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.mail_scheduler[0].email}"
}

resource "google_cloud_scheduler_job" "mail_poll" {
  count       = var.mail_enabled && var.mail_image != null ? 1 : 0
  project     = google_project.main.project_id
  region      = var.region
  name        = "mail-ingestion-poll"
  description = "Start an ingestion run for each account so new mail is picked up"
  schedule    = var.mail_poll_schedule
  time_zone   = "Etc/UTC"
  http_target {
    uri         = "${local.mail_url}/ingestion/poll"
    http_method = "POST"
    headers     = { "Content-Type" = "application/json" }
    oidc_token {
      service_account_email = google_service_account.mail_scheduler[0].email
      audience              = local.mail_url
    }
  }
  depends_on = [google_cloud_run_v2_service_iam_member.mail_scheduler]
}
