# Terraform for S3-landingssone

Dette oppsettet administrerer konfigurasjonen til den eksisterende S3-bøtten for landingssonen: selve bøtten, blokkering av offentlig tilgang, object ownership, versjonering, server-side-kryptering og lifecycle-regelen. Bøtten ble importert, ikke gjenskapt. Etter apply viste en ny Terraform-plan `0 add, 0 change, 0 destroy`.

IAM, Databricks storage credential, external location, external volume og Lakeflow-jobben administreres fortsatt manuelt. IaC er derfor delvis implementert. Terraform og Databricks Asset Bundles er neste steg for disse ressursene.

## Lokal state og import

Ressursene finnes allerede i AWS og er importert til Terraform. Terraform-state er foreløpig lokal, ignorert av Git og ikke committet. State- og planfiler kan inneholde sensitiv informasjon og skal aldri committes.

Kjør kommandoene fra denne katalogen. Profilen er ikke hardkodet i provider-konfigurasjonen; AWS SDKs standard credential chain brukes.

```bash
AWS_PROFILE=clubdata-iac terraform init
AWS_PROFILE=clubdata-iac terraform validate

AWS_PROFILE=clubdata-iac terraform import aws_s3_bucket.landing clubdata-platform-landing-portfolio
AWS_PROFILE=clubdata-iac terraform import aws_s3_bucket_public_access_block.landing clubdata-platform-landing-portfolio
AWS_PROFILE=clubdata-iac terraform import aws_s3_bucket_ownership_controls.landing clubdata-platform-landing-portfolio
AWS_PROFILE=clubdata-iac terraform import aws_s3_bucket_versioning.landing clubdata-platform-landing-portfolio
AWS_PROFILE=clubdata-iac terraform import aws_s3_bucket_server_side_encryption_configuration.landing clubdata-platform-landing-portfolio
AWS_PROFILE=clubdata-iac terraform import aws_s3_bucket_lifecycle_configuration.landing clubdata-platform-landing-portfolio

AWS_PROFILE=clubdata-iac terraform plan -detailed-exitcode
```

Gjennomgå alltid hele `terraform plan` før en eventuell `terraform apply`. Remote state, IAM og Databricks-ressurser er senere steg og skal innføres separat.

GitHub Actions kjører `terraform fmt -check -recursive`, `terraform init -backend=false -input=false` og `terraform validate` uten AWS-credentials. Automatisk Terraform-plan og apply er ikke implementert.