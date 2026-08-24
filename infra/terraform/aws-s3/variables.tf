variable "aws_region" {
  description = "AWS region containing the S3 landing bucket."
  type        = string
  default     = "us-west-2"
}

variable "bucket_name" {
  description = "Name of the existing S3 landing bucket."
  type        = string
  default     = "clubdata-platform-landing-portfolio"
}