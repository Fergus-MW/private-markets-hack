output "project_id" { value = google_project.main.project_id }
output "image_repository" { value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.services.repository_id}" }
output "surrealdb_private_ip" { value = google_compute_instance.database.network_interface[0].network_ip }
output "ingestion_url" { value = try(google_cloud_run_v2_service.ingestion[0].uri, null) }
