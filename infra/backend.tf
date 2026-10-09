# Create the bucket and lock table first, then fill in values (or pass via -backend-config).
terraform {
  backend "s3" {
    bucket         = "REPLACE-tfstate-bucket"
    key            = "project1/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "REPLACE-tfstate-lock"
    encrypt        = true
  }
}
