output "service_url" {
  description = "Public URL of the Cloud Run service. Set this as ALPHABRIEF_API_URL on Vercel."
  value       = google_cloud_run_v2_service.api.uri
}

output "artifact_registry_repository" {
  description = "Docker repository to push the API image to."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.api.repository_id}"
}

output "service_account_email" {
  description = "Service account the revision runs as."
  value       = google_service_account.api.email
}

output "secret_ids" {
  description = "Secret Manager secrets to populate before the first real run."
  value       = [for secret in google_secret_manager_secret.app : secret.secret_id]
}
