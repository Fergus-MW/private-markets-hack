# Public consent screen. It creates one Secret Manager secret per connected
# account and grants the connector job read access to it.

variable "frontend_image" {
  description = "Frontend image URI, preferably pinned by digest. Null provisions identity and secrets only."
  type        = string
  default     = null
}

variable "frontend_public_origin" {
  description = "Final HTTPS origin of the frontend. Set from the service URL after the first deploy; must match the OAuth client's redirect URI."
  type        = string
  default     = null
}

resource "google_service_account" "frontend" {
  count      = var.frontend_image == null && var.frontend_public_origin == null ? 0 : 1
  account_id = "frontend"
  depends_on = [google_project_service.apis]
}

# Split deliberately. `create` cannot be constrained by a condition (the resource
# at create time is the project), but writing versions and, above all, setting IAM
# policy are confined to connector-u-* so this identity can never widen access to
# the database password secrets.
resource "google_project_iam_custom_role" "frontend_connect" {
  count       = length(google_service_account.frontend)
  role_id     = "frontendConnectorSecrets"
  title       = "Frontend per-account connector secrets"
  permissions = ["secretmanager.secrets.create"]
}

resource "google_project_iam_custom_role" "frontend_connect_scoped" {
  count       = length(google_service_account.frontend)
  role_id     = "frontendConnectorSecretsScoped"
  title       = "Frontend per-account connector secret writes"
  permissions = ["secretmanager.secrets.get", "secretmanager.secrets.setIamPolicy", "secretmanager.versions.add"]
}

resource "google_project_iam_member" "frontend_connect" {
  count   = length(google_service_account.frontend)
  project = google_project.main.project_id
  role    = google_project_iam_custom_role.frontend_connect[0].id
  member  = "serviceAccount:${google_service_account.frontend[0].email}"
}

resource "google_project_iam_member" "frontend_connect_scoped" {
  count   = length(google_service_account.frontend)
  project = google_project.main.project_id
  role    = google_project_iam_custom_role.frontend_connect_scoped[0].id
  member  = "serviceAccount:${google_service_account.frontend[0].email}"
  condition {
    title      = "Per-account connector secrets only"
    expression = "resource.name.startsWith(\"projects/${google_project.main.number}/secrets/connector-u-\")"
  }
}

# Runtime configuration that must not appear in environment variables.
resource "google_secret_manager_secret" "frontend" {
  for_each  = length(google_service_account.frontend) == 0 ? toset([]) : toset(["oauth-client-secret", "session-key"])
  secret_id = "frontend-${each.key}"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "frontend" {
  for_each  = google_secret_manager_secret.frontend
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.frontend[0].email}"
}

# Two-pass by necessity: PUBLIC_ORIGIN is this service's own URL. The first apply
# creates it un-configured (the app reports honest setup status rather than faking
# authorization); set frontend_public_origin from the output and apply again.
resource "google_cloud_run_v2_service" "frontend" {
  count = var.frontend_image == null ? 0 : 1
  # Cloud Build owns application image updates after initial provisioning.
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
  name                = "frontend"
  location            = var.region
  project             = google_project.main.project_id
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  template {
    timeout         = "900s"
    service_account = google_service_account.frontend[0].email
    containers {
      image = var.frontend_image
      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }
      env {
        name = "GRAPH_IDENTITY_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.graph_identity.secret_id
            version = google_secret_manager_secret_version.graph_identity.version
          }
        }
      }
      dynamic "env" {
        for_each = {
          INGESTION_URL              = try(google_cloud_run_v2_service.ingestion[0].uri, "")
          PUBLIC_ORIGIN              = coalesce(var.frontend_public_origin, "")
          GOOGLE_OAUTH_CLIENT_ID     = var.frontend_oauth_client_id
          CONNECTOR_PROJECT          = google_project.main.project_id
          CONNECTOR_SERVICE_ACCOUNTS = join(",", [for account in google_service_account.connector : account.email])
          CONNECTOR_SERVICE_ACCOUNT  = try(google_service_account.connector[keys(local.connector_jobs)[0]].email, "")
        }
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = {
          GOOGLE_OAUTH_CLIENT_SECRET = "frontend-oauth-client-secret"
          SESSION_KEY                = "frontend-session-key"
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
  depends_on = [google_secret_manager_secret_iam_member.graph_identity_frontend, google_cloud_run_v2_service_iam_member.frontend_graph, google_secret_manager_secret_iam_member.frontend, google_project_iam_member.frontend_connect]
}

variable "frontend_oauth_client_id" {
  description = "Web application OAuth client ID. Not a secret, unlike the client secret."
  type        = string
  default     = ""
}

# The consent screen and Google's browser redirect must both reach this service.
resource "google_cloud_run_v2_service_iam_member" "frontend_public" {
  count    = length(google_cloud_run_v2_service.frontend)
  project  = google_project.main.project_id
  location = var.region
  name     = google_cloud_run_v2_service.frontend[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "frontend_url" {
  value = try(google_cloud_run_v2_service.frontend[0].uri, null)
}
