variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "region" {
  type        = string
  description = "Cloud Run region. Pick one close to your users."
  default     = "us-central1"
}

variable "service_name" {
  type        = string
  description = "Cloud Run service name."
  default     = "alphabrief-api"
}

variable "image" {
  type        = string
  description = <<-EOT
    Fully-qualified container image, e.g.
    us-central1-docker.pkg.dev/PROJECT/alphabrief-api-images/api:v1
  EOT
}

variable "web_origin" {
  type        = string
  description = "Origin allowed by CORS — your Vercel deployment."
  default     = "https://alphabrief.vercel.app"
}

variable "default_watchlist" {
  type        = string
  description = "Comma-separated default watchlist."
  default     = "AAPL,MSFT,NVDA,TSLA,AMZN"
}

variable "max_instances" {
  type        = number
  description = "Upper bound on concurrent instances. Keep small to stay free."
  default     = 2
}

variable "request_timeout_seconds" {
  type        = number
  description = "Cloud Run request timeout. Runs stream for a while."
  default     = 900
}

variable "max_iterations" {
  type        = number
  description = "Supervisor hard iteration cap."
  default     = 15
}

variable "token_budget_usd" {
  type        = number
  description = "Per-run spend ceiling before BUDGET_ABORT."
  default     = 0.50
}

variable "allow_public_access" {
  type        = bool
  description = "Grant run.invoker to allUsers so the Vercel front end can call it."
  default     = true
}
