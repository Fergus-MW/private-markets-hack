# Project graphs live in a separate namespace. The provisioning identity cannot
# access the canonical namespace; per-project workers receive DB-scoped users.
resource "random_password" "project_provisioner" {
  length  = 40
  special = false
}
resource "random_password" "project_secret" {
  length  = 64
  special = false
}
resource "google_secret_manager_secret" "project_provisioner" {
  secret_id = "surrealdb-project-provisioner-password"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}
resource "google_secret_manager_secret" "project_secret" {
  secret_id = "surrealdb-project-credential-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}
resource "google_secret_manager_secret_version" "project_provisioner" {
  secret      = google_secret_manager_secret.project_provisioner.id
  secret_data = random_password.project_provisioner.result
}
resource "google_secret_manager_secret_version" "project_secret" {
  secret      = google_secret_manager_secret.project_secret.id
  secret_data = random_password.project_secret.result
}
resource "google_secret_manager_secret_iam_member" "database_project_provisioner" {
  secret_id = google_secret_manager_secret.project_provisioner.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.database.email}"
}
resource "google_secret_manager_secret_iam_member" "ingestion_project_provisioner" {
  secret_id = google_secret_manager_secret.project_provisioner.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ingestion.email}"
}
resource "google_secret_manager_secret_iam_member" "ingestion_project_secret" {
  secret_id = google_secret_manager_secret.project_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ingestion.email}"
}

# A startup-script edit must never replace the data VM. This tracked migration
# also handles existing installations, and blocks application rollout on failure.
# The apply operator needs gcloud, IAP tunnel access and OS Login on the VM.
resource "terraform_data" "project_namespace" {
  triggers_replace = {
    instance            = google_compute_instance.database.instance_id
    root_version        = google_secret_manager_secret_version.root.version
    provisioner_version = google_secret_manager_secret_version.project_provisioner.version
    migration           = filesha256("${path.module}/scripts/bootstrap_project_namespace.py")
    runner              = filesha256("${path.module}/scripts/run_project_bootstrap.py")
  }

  provisioner "local-exec" {
    working_dir = path.module
    command     = "python3 scripts/run_project_bootstrap.py"
    environment = {
      WORKFLOW_PROJECT             = var.project_id
      WORKFLOW_ZONE                = var.zone
      WORKFLOW_VM                  = google_compute_instance.database.name
      WORKFLOW_ROOT_VERSION        = google_secret_manager_secret_version.root.version
      WORKFLOW_PROVISIONER_VERSION = google_secret_manager_secret_version.project_provisioner.version
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.root,
    google_secret_manager_secret_iam_member.database_project_provisioner,
    google_compute_firewall.iap,
  ]
}

# Service-to-service user assertions are separate from DB-derived credentials.
resource "random_password" "graph_identity" {
  length  = 64
  special = false
}
resource "google_secret_manager_secret" "graph_identity" {
  secret_id = "graph-identity-signing-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}
resource "google_secret_manager_secret_version" "graph_identity" {
  secret      = google_secret_manager_secret.graph_identity.id
  secret_data = random_password.graph_identity.result
}
resource "google_secret_manager_secret_iam_member" "graph_identity_ingestion" {
  secret_id = google_secret_manager_secret.graph_identity.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ingestion.email}"
}
resource "google_secret_manager_secret_iam_member" "graph_identity_connector" {
  for_each  = var.google_connectors
  secret_id = google_secret_manager_secret.graph_identity.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.connector[each.key].email}"
}
resource "google_secret_manager_secret_iam_member" "graph_identity_frontend" {
  count     = length(google_service_account.frontend)
  secret_id = google_secret_manager_secret.graph_identity.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.frontend[0].email}"
}
resource "google_cloud_run_v2_service_iam_member" "frontend_graph" {
  count    = (var.frontend_image != null || var.frontend_public_origin != null) && var.ingestion_image != null ? 1 : 0
  name     = google_cloud_run_v2_service.ingestion[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.frontend[0].email}"
}
