# SDS AWS EC2 + RDS reference profile

This directory is the public, secret-free deployment profile for the SustainabilityDataSpace API. It prepares infrastructure and operating contracts only. It does not authenticate to AWS, build or push an image, run Terraform plan/apply, populate secrets, migrate a database, change DNS, or activate trust.

This technical evaluation reference does not grant a right to operate SDS for
your own business or provide services to others. Any productive use requires a separate
written agreement with the SDS code rights holder; see "Alcance de uso previsto"
in the repository's root `README.md`. Cloud-effect approvals below are separate
from permission to use the product.

## Architecture and supported boundary

The profile creates this deliberately conservative topology:

```text
Internet -> public ALB (TLS) -> regional WAF -> one private EC2 host -> private Multi-AZ RDS PostgreSQL
                                            -> ECR/SSM/Secrets/Logs/KMS through VPC endpoints
```

There is no public EC2 address, SSH ingress, NAT Gateway or wildcard application egress. The application subnet reaches only RDS, interface endpoints, the S3 gateway endpoint, VPC DNS and Amazon Time Sync. The host is operated through SSM. RDS, EBS, ECR, Secrets Manager, logs and backup recovery points use customer-managed KMS keys.

This is infrastructure-resilient but not application-HA: there is exactly one API host and one Uvicorn process. The AWS runtime must not migrate: it reads the approved Alembic heads before first boot and on every restart and fails closed if they differ. Startup job reconciliation, overlapping writers and distributed rate limiting still require separate qualification. Replacement is stop-before-start and can cause downtime.

## Public/private separation

Tracked public files contain only generic infrastructure, validators and placeholders. Keep these items in a private operator overlay outside Git:

- AWS account identifiers, real CIDRs, DNS names, certificate and AMI selections;
- Terraform backend bucket/key/KMS details and all Terraform state;
- the exact ECR image digest and image-build/SBOM/signature evidence;
- secret values and the populated Secrets Manager version ID;
- database bootstrap, migration, smoke, rollback and recovery receipts;
- budgets, notification recipients, production approvals and incident evidence.

The `operator.example.json` values are documentation sentinels and are not deployable. `validate.py` reports `deployable_inputs=false` until all sentinel identities are replaced. It never makes cloud calls.

## Required private prerequisites

Before any plan, the operator must independently establish:

1. A dedicated non-root AWS role/session and an approved region and cost boundary.
2. An encrypted, versioned S3 Terraform backend with locking. Supply its bucket, key, region, KMS key and `use_lockfile=true` through private backend configuration.
3. A prevalidated ACM certificate in the selected region.
4. An approved, patched AMI with Docker, Python 3, AWS CLI v2 and SSM Agent already installed. The private subnet has no package-manager Internet path.
5. A foundation apply with `runtime_activation_enabled=false`, separately approved after plan review, creates the immutable ECR repository and empty runtime-secret container but deliberately creates no EC2 runtime or target attachment.
6. An SDS API image built from an exact clean commit, scanned, accompanied by SBOM/provenance, pushed to that immutable ECR repository, and selected by `repository@sha256:digest`.
7. A populated version of the module-created runtime secret. Its exact JSON key set is:
   `ALLOWED_ORIGINS`, `APP_ENV`, `DATABASE_URL`, `EXPORT_SIGNING_SECRET`, `JWT_SECRET_KEY`, `LOG_LEVEL`, `REQUIRE_DATABASE`, `SCHEMA_MIGRATIONS_EXTERNALLY_MANAGED`, `TRUSTED_PROXY_IPS`, `USE_POSTGRES_UNITS`, `VALUE_REVISION_API_ENABLED`, `VALUE_REVISION_DUAL_WRITE_ENABLED`, `VALUE_REVISION_PRIMARY_READ_PATH`.
   `SCHEMA_MIGRATIONS_EXTERNALLY_MANAGED` must equal `true`; the runtime does not have migration authority. This exact flag is required by the bootstrap, not a substitute for a qualified migration channel.
   The final three values must be `true`, `true`, and `revision`, respectively; the bootstrap rejects any extra or missing keys.
   Set `TRUSTED_PROXY_IPS` to the reviewed private ALB/VPC proxy CIDR boundary so the API derives rate-limit keys from the ALB-appended client chain rather than treating the load balancer as every client.
   The image starts Uvicorn with `--no-proxy-headers`: only the application may interpret X-Forwarded-For, after checking the socket peer. Qualify ALB header append behavior, proxy peer CIDRs and effective startup arguments on the real host before activation; the offline tests cannot establish that contract.
   The request-body gate reserves at most eight buffered-body slots per process (each up to the configured body limit), rejects excess admission and enforces a 30-second total read deadline. Size the EC2 host and edge request limit against measured peak memory and upload time before approving activation; offline assertions do not establish host capacity.
8. A least-privilege application database user created using a separately controlled bootstrap process. The runtime role can read only the application secret; it cannot read the RDS master secret.
9. A separately controlled private network path to the RDS instance for the reviewed migration, a snapshot before any irreversible revision, and a tested backup/restore and rollback decision. The runtime EC2 host is not the migration channel; if no qualified private path exists, keep `runtime_activation_enabled=false`.

Never put secret values in tfvars, environment examples, CI variables that are printed, Terraform state or command arguments.

## Offline qualification (no AWS calls)

From the repository root:

```bash
python api/deploy/aws-ec2-rds/validate.py api/deploy/aws-ec2-rds/operator.example.json
python -m pytest api/tests/test_aws_public_profile.py -q
make aws-profile-check  # includes Terraform 1.16.3 mock-only plans
```

Format and validate Terraform only in a disposable copy so `.terraform/` and generated lock material do not contaminate the repository:

```bash
work="$(mktemp -d)"
cp -R api/deploy/aws-ec2-rds/terraform "$work/terraform"
terraform -chdir="$work/terraform" fmt -check -recursive
terraform -chdir="$work/terraform" init -backend=false
terraform -chdir="$work/terraform" validate
terraform -chdir="$work/terraform" test -filter=activation.tftest.hcl
rm -rf "$work"
```

The checked-in test file uses only a mocked AWS provider and explicitly selects `command = plan` for every case; it does not use AWS credentials, remote state or cloud APIs. CI runs structural Python tests, `terraform validate`, and the mocked `terraform test` cases. These offline checks do not prove account permissions, real-provider plan safety, image availability or deployability.

## Controlled plan and release sequence

Each numbered effect is a separate approval and evidence boundary:

1. Build and scan the exact API image locally; do not push.
2. Populate private backend/operator configuration and run the offline validator.
3. With `runtime_activation_enabled=false`, authenticate read-only and run a foundation `terraform plan -out=<private-plan>`; parse `terraform show -json` and reject any EC2 instance/target attachment, unapproved replacement, deletion, wildcard IAM or wider network rule.
4. Obtain explicit cost/scope approval before the foundation `terraform apply`. This creates paid network/database/ALB/WAF/endpoints plus empty ECR/secret resources, but no API host.
5. Populate a new application-secret version without reading it into logs or Terraform.
6. Bootstrap the least-privilege database principal through the separately controlled channel; remove bootstrap access afterwards.
7. Push the exact image digest to immutable ECR only after separate publication approval.
8. Through that separately approved private path, run and verify the reviewed Alembic migration **before** runtime activation; record the exact database and image heads. Never run it via first boot or application startup. If the private path is unavailable, stop here.
9. Set the exact image digest and secret version in the private overlay and set `runtime_activation_enabled=true`. Produce and review a new activation plan and obtain separate approval before applying it. The public module has **no target attachment or traffic-approval variables**; do not use `-target` to bypass the phases.
10. The activation apply creates a private EC2 host **without** attaching it to the ALB target group. Its unit reads the pinned secret, checks the database heads in the pinned image before replacing a container, and starts the read-only/capability-dropped API only on an exact match. It repeats the read-only check at each restart; application startup also verifies heads and does not run Alembic.
11. Verify the host and API privately through an approved SSM path: `/healthz`, authenticated `/ready`, authentication/session invalidation, tenant isolation, representative DB reads/writes and CloudWatch/WAF/backup telemetry. Offline tests cannot establish this host, network or database proof.
12. Traffic promotion is **outside this repository profile**. Leave the ALB target group empty until Sygris qualifies a distinct private promoter with the sole target-registration authority; the foundation/deployment role must be unable to register targets or redirect the listener. Before any registration, that promoter must verify an independently issued, expiring one-use approval bound to account/region, target group and existing instance ID/AMI, ECR manifest digest, secret VersionId, database identity and migration heads, and the exact private smoke receipt; re-read the live bindings, reserve approval durably, register once, then read back registration and health. Detach before replacement and require a new smoke and approval. This public module does not implement or qualify that promoter or its effective IAM permissions. Without it, **no traffic attachment is authorized**. Keep the receipts and identities in the private operator overlay, not the public repository.

Image provenance: `api/Dockerfile` pins the upstream Python 3.10 slim
Linux/amd64 manifest digest verified from Docker Hub; `api/requirements.lock`
pins selected Linux/amd64 wheels for the Python 3.10 Docker image and Python
3.12 hosted native runtime by version and SHA-256. A local hashed image build
and `pip check` qualify installation mechanics, not the release or AWS runtime.
Public builds may fetch only hash-verified wheels from the configured package
index. The controlled release build must instead use an externally reviewed
wheelhouse with `pip --no-index --require-hashes`,
record the clean source commit, Dockerfile and base digest, lock and wheelhouse
digests, SBOM and scans, built OCI digest and ECR readback in an independently
verified receipt. Re-review the base and lock for security updates before
release. A digest identifies an input; it does not prove byte-identical builds
or current vulnerability status. Keep wheelhouse contents and receipts outside
the public repository until explicitly sanitized and approved.

The public module intentionally does not include a GitHub workflow with AWS write permissions. If Sygris later adds OIDC publication/deployment workflows, use separate publisher and deployer roles with exact repository/ref/environment claims. A publisher may push ECR only; a deployer may apply an approved plan and invoke a pinned SSM document only. Neither role should receive both authorities.

## Rollback and disaster recovery

Application rollback means stop the new unit, select a previously accepted image digest and compatible secret version, start it, and rerun health/readiness and tenant-bound smoke. Never retry a migration ambiguously or run two release writers concurrently.

Database rollback is snapshot restore, not Alembic downgrade, once migration 046 has been applied. Restore to a new RDS instance, verify integrity and semantic projection privately, then perform a reviewed endpoint/secret cutover. AWS Backup provides daily RDS recovery points; native RDS automated backups and a final snapshot remain enabled as separate layers.

Destroy is intentionally obstructed by RDS and ALB deletion protection plus Terraform `prevent_destroy`. A paid sandbox still requires a separately approved teardown plan. KMS deletion has a mandatory waiting period and must be tracked as a residual after billable resources are removed.

## Explicit non-claims

The profile has not been applied to any AWS account and contains no AWS qualification evidence. It does not prove production acceptance, DNS ownership, cost eligibility, recovery time, data migration, multi-instance safety, trust activation, or completion of SDS deliverables E7/E12/E13. Current release status remains NO-GO until an exact candidate passes local gates, clean-clone qualification, private account plan review and independent review.
