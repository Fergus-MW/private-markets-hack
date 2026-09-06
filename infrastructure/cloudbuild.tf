resource "google_project_service" "cloudbuild" {
  project            = google_project.main.project_id
  service            = "cloudbuild.googleapis.com"
  disable_on_destroy = false
}

resource "google_service_account" "build" {
  account_id   = "ingestion-build"
  display_name = "Ingestion build and deployment"
  depends_on   = [google_project_service.cloudbuild]
}

resource "google_project_iam_member" "build_logs" {
  project = google_project.main.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.build.email}"
}

resource "google_artifact_registry_repository_iam_member" "build_push" {
  project    = google_project.main.project_id
  location   = var.region
  repository = google_artifact_registry_repository.services.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.build.email}"
}

resource "google_cloud_run_v2_service_iam_member" "build_deploy" {
  # Release IAM targets a provisioned service; it must not pull runtime drift into a CD apply.
  count    = var.ingestion_image == null ? 0 : 1
  name     = "document-ingestion"
  location = var.region
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.build.email}"
}

resource "google_service_account_iam_member" "build_act_as" {
  service_account_id = google_service_account.ingestion.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.build.email}"
}

# Frontend releases can update only this service and use its runtime identity.
resource "google_cloud_run_v2_service_iam_member" "build_deploy_frontend" {
  count    = local.frontend_release_enabled ? 1 : 0
  project  = google_project.main.project_id
  name     = "frontend"
  location = var.region
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.build.email}"
}

resource "google_service_account_iam_member" "build_act_as_frontend" {
  count              = local.frontend_release_enabled ? 1 : 0
  service_account_id = google_service_account.frontend[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.build.email}"
}

# Bootstrap this connection with gcloud and import it (see CLOUD_BUILD.md).
# GitHub authorization is completed using the connection's installation URL.
# OAuth credentials remain in Secret Manager, not in this repository.
resource "google_cloudbuildv2_connection" "github" {
  # Only needed by the trigger; creating it requires the gcloud/GitHub App
  # bootstrap below, so keep it gated with everything else that uses it.
  count    = var.enable_github_trigger && var.github_connection_mode == "regional" ? 1 : 0
  location = var.region
  name     = "github"
  github_config {}
  lifecycle {
    ignore_changes = [github_config]
  }
  depends_on = [google_project_service.cloudbuild]
}

variable "enable_github_trigger" {
  description = "Enable release triggers after authorizing the selected GitHub connection mode."
  type        = bool
  default     = false
}

resource "google_cloudbuildv2_repository" "main" {
  count             = var.enable_github_trigger && var.github_connection_mode == "regional" ? 1 : 0
  name              = "private-markets-hack"
  location          = var.region
  parent_connection = google_cloudbuildv2_connection.github[0].id
  remote_uri        = "https://github.com/Fergus-MW/private-markets-hack.git"
}

resource "google_cloudbuild_trigger" "ingestion" {
  count           = var.enable_github_trigger && var.ingestion_image != null ? 1 : 0
  name            = "ingestion-main"
  location        = local.github_trigger_location
  service_account = google_service_account.build.id
  filename        = "cloudbuild.yaml"
  included_files  = ["services/ingestion/**", "cloudbuild.yaml"]
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
  depends_on = [google_project_iam_member.build_logs,
    google_artifact_registry_repository_iam_member.build_push,
    google_cloud_run_v2_service_iam_member.build_deploy,
  google_service_account_iam_member.build_act_as]
}

resource "google_cloudbuild_trigger" "frontend" {
  count           = var.enable_github_trigger && local.frontend_release_enabled ? 1 : 0
  name            = "frontend-main"
  location        = local.github_trigger_location
  service_account = google_service_account.build.id
  filename        = "cloudbuild-frontend.yaml"
  included_files  = ["frontend/**", "cloudbuild-frontend.yaml"]
  substitutions = {
    _REGION   = var.region
    _SERVICES = "frontend"
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
  depends_on = [
    google_project_iam_member.build_logs,
    google_artifact_registry_repository_iam_member.build_push,
    google_cloud_run_v2_service_iam_member.build_deploy_frontend,
    google_service_account_iam_member.build_act_as_frontend,
  ]
}

# The Cloud Build service agent creates the OAuth secret and assigns its
# resource-level access policy during GitHub App installation.
resource "google_project_iam_custom_role" "connection_secrets" {
  project     = google_project.main.project_id
  role_id     = "cloudBuildConnectionSecrets"
  title       = "Cloud Build connection secret setup"
  permissions = ["secretmanager.secrets.create", "secretmanager.secrets.setIamPolicy"]
}
resource "google_project_iam_member" "connection_secrets" {
  project    = google_project.main.project_id
  role       = google_project_iam_custom_role.connection_secrets.name
  member     = "serviceAccount:service-${google_project.main.number}@gcp-sa-cloudbuild.iam.gserviceaccount.com"
  depends_on = [google_project_service.cloudbuild]
}

moved {
  from = google_cloud_run_v2_service_iam_member.build_deploy
  to   = google_cloud_run_v2_service_iam_member.build_deploy[0]
}

variable "github_connection_mode" {
  description = "github-app reuses a connected GitHub App repository; regional uses a second-generation host connection."
  type        = string
  default     = "regional"
  validation {
    condition     = contains(["github-app", "regional"], var.github_connection_mode)
    error_message = "github_connection_mode must be github-app or regional."
  }
}

variable "frontend_existing_for_cd" {
  description = "Manage releases for the existing frontend service without importing or changing its runtime configuration. Its runtime service account must already be managed."
  type        = bool
  default     = false
}

locals {
  github_trigger_location  = var.github_connection_mode == "github-app" ? "global" : var.region
  frontend_release_enabled = var.frontend_image != null || var.frontend_existing_for_cd
}
