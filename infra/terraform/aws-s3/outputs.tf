output "bucket_name" {
  description = "Name of the Terraform-managed S3 landing bucket."
  value       = aws_s3_bucket.landing.id
}

output "bucket_region" {
  description = "Region of the Terraform-managed S3 landing bucket."
  value       = aws_s3_bucket.landing.region
}