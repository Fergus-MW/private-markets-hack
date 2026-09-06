variable "model_gateway_image" {
  description = "Gemini gateway image URI, preferably pinned by digest."
  type        = string
  default     = null
}

resource "google_service_account" "model_gateway" {
  count        = var.model_gateway_image == null ? 0 : 1
  account_id   = "model-gateway"
  display_name = "Private Gemini gateway"
  depends_on   = [google_project_service.apis]
}

resource "google_cloud_run_v2_service" "model_gateway" {
  count               = var.model_gateway_image == null ? 0 : 1
  name                = "model-gateway"
  project             = google_project.main.project_id
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
  scaling {
    min_instance_count    = 1
    manual_instance_count = 0
  }
  template {
    service_account                  = google_service_account.model_gateway[0].email
    timeout                          = "180s"
    max_instance_request_concurrency = 32
    scaling {
      min_instance_count = 1
      max_instance_count = 1
    }
    containers {
      image = var.model_gateway_image
      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = google_project.main.project_id
      }
      env {
        name  = "GEMINI_MODEL"
        value = var.mail_gemini_model
      }
      env {
        name  = "MODEL_GATEWAY_INITIAL_WINDOW"
        value = "4"
      }
      env {
        name  = "MODEL_GATEWAY_MAX_WINDOW"
        value = "32"
      }
      startup_probe {
        initial_delay_seconds = 1
        period_seconds        = 2
        failure_threshold     = 30
        http_get {
          path = "/healthz"
        }
      }
    }
  }
  depends_on = [google_project_iam_member.mail_models]
}

resource "google_cloud_run_v2_service_iam_member" "model_gateway_invoker" {
  for_each = var.model_gateway_image == null || !var.mail_enabled ? {} : {
    mail      = google_service_account.mail[0].email
    ingestion = google_service_account.ingestion.email
  }
  name     = google_cloud_run_v2_service.model_gateway[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${each.value}"
}

resource "google_service_account_iam_member" "model_gateway_build" {
  count              = var.model_gateway_image == null ? 0 : 1
  service_account_id = google_service_account.model_gateway[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.build.email}"
}

resource "google_cloud_run_v2_service_iam_member" "model_gateway_build" {
  count    = var.model_gateway_image == null ? 0 : 1
  name     = google_cloud_run_v2_service.model_gateway[0].name
  location = var.region
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.build.email}"
}

output "model_gateway_url" {
  value = try(google_cloud_run_v2_service.model_gateway[0].uri, null)
}
