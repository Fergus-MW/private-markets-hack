resource "google_project" "main" {
  name                = "private markets hack"
  project_id          = var.project_id
  org_id              = var.org_id
  billing_account     = var.billing_account
  auto_create_network = false
  deletion_policy     = "PREVENT"
}
locals {
  apis = toset(["compute.googleapis.com", "run.googleapis.com", "artifactregistry.googleapis.com", "secretmanager.googleapis.com", "iam.googleapis.com", "cloudresourcemanager.googleapis.com"])
}
resource "google_project_service" "apis" {
  for_each           = local.apis
  project            = google_project.main.project_id
  service            = each.value
  disable_on_destroy = false
}
resource "google_compute_network" "main" {
  name                    = "private-markets"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}
resource "google_compute_subnetwork" "main" {
  name                     = "private-markets"
  ip_cidr_range            = "10.42.0.0/24"
  region                   = var.region
  network                  = google_compute_network.main.id
  private_ip_google_access = true
}
resource "google_compute_router" "main" {
  name    = "private-markets"
  network = google_compute_network.main.id
}
resource "google_compute_router_nat" "main" {
  name                               = "private-markets"
  router                             = google_compute_router.main.name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}
resource "google_compute_firewall" "database" {
  name        = "ingestion-to-surrealdb"
  network     = google_compute_network.main.name
  source_tags = ["ingestion"]
  target_tags = ["surrealdb"]
  allow {
    protocol = "tcp"
    ports    = ["8000"]
  }
}
resource "google_compute_firewall" "iap" {
  name          = "iap-ssh-surrealdb"
  network       = google_compute_network.main.name
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["surrealdb"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
resource "google_service_account" "database" {
  account_id = "surrealdb"
  depends_on = [google_project_service.apis]
}
resource "google_service_account" "ingestion" {
  account_id = "document-ingestion"
  depends_on = [google_project_service.apis]
}
resource "random_password" "root" {
  length  = 40
  special = false
}
resource "random_password" "ingestion" {
  length  = 40
  special = false
}
resource "google_secret_manager_secret" "root" {
  secret_id = "surrealdb-root-password"
  replication {
    auto {
    }
  }
  depends_on = [google_project_service.apis]
}
resource "google_secret_manager_secret" "ingestion" {
  secret_id = "surrealdb-ingestion-password"
  replication {
    auto {
    }
  }
  depends_on = [google_project_service.apis]
}
resource "google_secret_manager_secret_version" "root" {
  secret      = google_secret_manager_secret.root.id
  secret_data = random_password.root.result
}
resource "google_secret_manager_secret_version" "ingestion" {
  secret      = google_secret_manager_secret.ingestion.id
  secret_data = random_password.ingestion.result
}
resource "google_secret_manager_secret_iam_member" "root" {
  secret_id = google_secret_manager_secret.root.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.database.email}"
}
resource "google_secret_manager_secret_iam_member" "database_ingestion" {
  secret_id = google_secret_manager_secret.ingestion.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.database.email}"
}
resource "google_secret_manager_secret_iam_member" "ingestion" {
  secret_id = google_secret_manager_secret.ingestion.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ingestion.email}"
}
resource "google_compute_disk" "data" {
  name = "surrealdb-data"
  type = "pd-balanced"
  size = 10
  zone = var.zone
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.apis]
}
resource "google_compute_resource_policy" "backup" {
  depends_on = [google_project_service.apis]
  name       = "surrealdb-daily"
  region     = var.region
  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "03:00"
      }
    }
    retention_policy {
      max_retention_days    = 7
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }
    snapshot_properties {
      storage_locations = [var.region]
    }
  }
}
resource "google_compute_disk_resource_policy_attachment" "backup" {
  name = google_compute_resource_policy.backup.name
  disk = google_compute_disk.data.name
  zone = var.zone
}
resource "google_compute_instance" "database" {
  name                = "surrealdb"
  machine_type        = "e2-small"
  zone                = var.zone
  deletion_protection = true
  tags                = ["surrealdb"]
  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 10
      type  = "pd-balanced"
    }
  }
  attached_disk {
    source      = google_compute_disk.data.id
    device_name = "surrealdb-data"
  }
  network_interface {
    subnetwork = google_compute_subnetwork.main.id
  }
  service_account {
    email  = google_service_account.database.email
    scopes = ["cloud-platform"]
  }
  metadata = {
    enable-oslogin = "TRUE"
  }
  metadata_startup_script = templatefile("${path.module}/startup.sh.tftpl", {
    project_id    = var.project_id
    surreal_image = var.surreal_image
    }
  )
  depends_on = [google_compute_router_nat.main, google_secret_manager_secret_version.root,
    google_secret_manager_secret_version.ingestion, google_secret_manager_secret_iam_member.root,
  google_secret_manager_secret_iam_member.database_ingestion]
}
resource "google_artifact_registry_repository" "services" {
  location      = var.region
  repository_id = "services"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}
resource "google_cloud_run_v2_service" "ingestion" {
  count               = var.ingestion_image == null ? 0 : 1
  name                = "document-ingestion"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  template {
    service_account                  = google_service_account.ingestion.email
    timeout                          = "900s"
    max_instance_request_concurrency = 1
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.main.name
        subnetwork = google_compute_subnetwork.main.name
        tags       = ["ingestion"]
      }
    }
    containers {
      image = var.ingestion_image
      resources {
        limits = {
          cpu = "2", memory = "4Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      ports {
        container_port = 8080
      }
      env {
        name  = "SURREAL_URL"
        value = "http://${google_compute_instance.database.network_interface[0].network_ip}:8000"
      }
      env {
        name  = "SURREAL_USER"
        value = "ingestion"
      }
      env {
        name = "SURREAL_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.ingestion.secret_id
            version = google_secret_manager_secret_version.ingestion.version
          }
        }
      }
      startup_probe {
        initial_delay_seconds = 10
        period_seconds        = 10
        failure_threshold     = 24
        http_get {
          path = "/healthz"
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.ingestion]
}
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  for_each = var.ingestion_image == null ? toset([]) : var.invoker_members
  name     = google_cloud_run_v2_service.ingestion[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = each.value
}
