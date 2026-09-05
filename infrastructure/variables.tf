variable "project_id" {
  description = "Globally unique project ID for the new project."
  type        = string
}
variable "org_id" {
  description = "Optional organisation numeric ID. Null creates a project without an organisation parent."
  type        = string
  default     = null
}
variable "billing_account" {
  description = "Billing account ID linked to the new project."
  type        = string
}
variable "region" {
  type    = string
  default = "europe-west2"
}
variable "zone" {
  type    = string
  default = "europe-west2-a"
}
variable "ingestion_image" {
  description = "Built ingestion image URI, preferably pinned by digest. Null creates infrastructure before the first image build."
  type        = string
  default     = null
}
variable "invoker_members" {
  description = "IAM members allowed to upload documents, e.g. user:name@example.com."
  type        = set(string)
  default     = []
}
variable "surreal_image" {
  type    = string
  default = "surrealdb/surrealdb:v2.3.10"
}
