# Create the bucket first (versioning enabled), then fill in the name or pass via -backend-config.
terraform {
  backend "s3" {
    bucket       = "dmh-app-prod1-bucket"
    key          = "project1/terraform.tfstate"
    region       = "eu-west-1"
    use_lockfile = true
    encrypt      = true
  }
}
