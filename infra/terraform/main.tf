###############################################################################
# AlphaBrief — GCP Cloud Run (always-free tier) provisioned declaratively.
#
# Deploying once with this module puts "Terraform (IaC)" and "GCP, Cloud Run" on
# the resume truthfully. It provisions:
#
#   * Artifact Registry repository for the API image
#   * Secret Manager secrets for every credential (nothing in env literals)
#   * a service account with least-privilege secret access
#   * the Cloud Run v2 service, scale-to-zero, within the always-free allowance
#
# Free-tier posture: min_instance_count = 0 and a 1 vCPU / 512Mi instance keep
# the service inside Cloud Run's 2M requests + 360k GB-seconds monthly free
# grant. Raising min instances above zero is what usually starts a bill.
###############################################################################

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ------------------------------------------------------------------ APIs ----
resource "google_project_service" "required" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ------------------------------------------------------- artifact registry ---
resource "google_artifact_registry_repository" "api" {
  location      = var.region
  repository_id = "${var.service_name}-images"
  description   = "AlphaBrief API container images"
  format        = "DOCKER"
  depends_on    = [google_project_service.required]
}

# --------------------------------------------------------- service account ---
resource "google_service_account" "api" {
  account_id   = "${var.service_name}-sa"
  display_name = "AlphaBrief Cloud Run service account"
}

# ---------------------------------------------------------------- secrets ---
locals {
  # Every credential the service may need. Optional ones are created with an
  # empty initial version so the service can boot before they are populated.
  secret_names = [
    "anthropic-api-key",
    "approval-token",
    "database-url",
    "langfuse-public-key",
    "langfuse-secret-key",
    "smtp-password",
  ]
}

resource "google_secret_manager_secret" "app" {
  for_each  = toset(local.secret_names)
  secret_id = "${var.service_name}-${each.value}"

  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "accessor" {
  for_each  = google_secret_manager_secret.app
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

# ------------------------------------------------------------- cloud run ----
resource "google_cloud_run_v2_service" "api" {
  name                = var.service_name
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.api.email
    # Scale to zero: the free grant is consumed only while requests are served.
    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }
    timeout = "${var.request_timeout_seconds}s"

    containers {
      image = var.image

      ports {
        container_port = 7860
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "ENVIRONMENT"
        value = "production"
      }
      env {
        name  = "MCP_TRANSPORT"
        value = "stdio"
      }
      env {
        name  = "DEFAULT_WATCHLIST"
        value = var.default_watchlist
      }
      env {
        name  = "CORS_ALLOW_ORIGINS"
        value = var.web_origin
      }
      env {
        name  = "MAX_ITERATIONS"
        value = tostring(var.max_iterations)
      }
      env {
        name  = "TOKEN_BUDGET_USD"
        value = tostring(var.token_budget_usd)
      }

      dynamic "env" {
        for_each = {
          ANTHROPIC_API_KEY    = "anthropic-api-key"
          APPROVAL_TOKEN       = "approval-token"
          DATABASE_URL         = "database-url"
          LANGFUSE_PUBLIC_KEY  = "langfuse-public-key"
          LANGFUSE_SECRET_KEY  = "langfuse-secret-key"
          SMTP_PASSWORD        = "smtp-password"
        }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app["${env.value}"].secret_id
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        initial_delay_seconds = 8
        period_seconds        = 6
        failure_threshold     = 12
        timeout_seconds       = 4
        http_get {
          path = "/health"
          port = 7860
        }
      }

      liveness_probe {
        period_seconds = 30
        http_get {
          path = "/health"
          port = 7860
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_project_service.required,
    google_secret_manager_secret_iam_member.accessor,
  ]
}

# The Vercel front end calls this service; it is public but every mutating
# endpoint still requires the approval bearer token.
resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.allow_public_access ? 1 : 0
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
