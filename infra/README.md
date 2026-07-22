# Production hosting (AWS)

Closes `docs/ROADMAP.md` S3: `docker-compose.yml` is a real, correct
single-instance local/dev setup, and stays exactly that - this is the
separate, real production target, not a replacement for local dev.

## What this is, and isn't

**Is:** real, deployable Terraform for the actual production shape -
managed Postgres (RDS) with automated backups and storage autoscaling,
managed Redis (ElastiCache), the app + dashboard + client-portal each
running as autoscaled ECS Fargate services behind one ALB, secrets in AWS
Secrets Manager (the exact thing `app/secrets_provider.py`'s
`AWSSecretsManagerProvider` was already built to read), and a GitHub
Actions pipeline that builds/pushes/deploys on every merge to `main` via
OIDC (no long-lived AWS keys stored anywhere).

**Isn't:** applied against a real AWS account yet - none exists for this
project. Every file here is written and `terraform validate`-clean, but
nobody has run `terraform apply` against real infrastructure. Same status
as this codebase's other "real client, unexercised against a live
account" integrations (Google Route Optimization, Rippling, Twilio) -
see `docs/ROADMAP.md`.

## Layout

```
infra/
  bootstrap/   One-time: the S3 bucket + DynamoDB table infra/aws/'s own
               remote state lives in. Apply this exactly once, first.
  aws/         Everything else - VPC, RDS, ElastiCache, ECS, ALB, Secrets
               Manager, S3 (photo uploads), ECR, the GitHub OIDC deploy role.
```

## First-time setup, in order

1. **Bootstrap remote state** (once, ever):
   ```bash
   cd infra/bootstrap
   terraform init
   terraform apply
   ```

2. **Apply the main stack**:
   ```bash
   cd infra/aws
   terraform init
   terraform plan   # read this before the next line - it provisions a real RDS instance, real ALB, etc.
   terraform apply
   ```
   First apply builds everything except real traffic - the ECS services
   come up, but with whatever image `var.app_image_tag` defaults to
   (`latest`), which doesn't exist in ECR yet. That's expected; the first
   real deploy (step 4) is what actually gives them something to run.

3. **Point a real domain at it.** `terraform output alb_dns_name` gives
   the ALB's DNS name - create `CNAME`/`ALIAS` records for
   `api.lmxit.com`, `ops.lmxit.com`, `portal.lmxit.com` pointing at it
   (wherever the domain is actually registered - this is an
   account/ownership step, not something Terraform can do without
   already owning the domain in Route 53). Then request an ACM
   certificate for those names and add an HTTPS listener to
   `infra/aws/alb.tf` using it - not automated here for the same reason.

4. **Wire up CI/CD**:
   - `terraform output github_actions_deploy_role_arn` → set as the
     `AWS_DEPLOY_ROLE_ARN` repository variable (Settings → Secrets and
     variables → Actions → Variables).
   - Push to `main` - `.github/workflows/deploy.yml` builds, pushes, and
     deploys all three images automatically once CI passes.

5. **Run the first migration** (this stack deliberately doesn't run
   migrations automatically on every task start - see `ecs.tf`'s comment
   on why):
   ```bash
   aws ecs run-task --cluster lmx-prod-cluster --task-definition lmx-prod-app \
     --launch-type FARGATE --network-configuration '...' \
     --overrides '{"containerOverrides":[{"name":"app","command":["alembic","upgrade","head"]}]}'
   ```
   (Fill in the real `--network-configuration` from `terraform output` -
   the exact subnet/security-group IDs. This is a one-off `run-task`, not
   part of the standing service.)

6. **Fill in real third-party credentials** once each account exists
   (`docs/ROADMAP.md` B4/B5, E1): `aws secretsmanager put-secret-value
   --secret-id $(terraform output -raw secrets_manager_secret_arn) ...`
   with the updated JSON blob. `secrets.tf`'s `ignore_changes` means a
   future `terraform apply` won't reset these back to placeholders.

## Ongoing deploys

Just `git push` to `main` once CI passes - `.github/workflows/deploy.yml`
handles the rest. A rollback is re-running that workflow
(`workflow_dispatch`) against an older commit; every image tag ever built
still exists in ECR (`ecr.tf`'s repos are `IMMUTABLE`).

## Real, named gaps

- **No staging environment.** Everything here is parameterized by
  `name_prefix` (`variables.tf`) specifically so a second environment is
  "copy this directory, change one variable, apply to a second AWS
  account or a different region" - not built because there's no team
  size yet that benefits from one (`docs/ROADMAP.md` B1).
- **No NAT Gateway** - `vpc.tf`'s docstring covers the real cost/purity
  tradeoff this is.
- **`db_multi_az` defaults to `false`.** One flag, in `variables.tf`, to
  flip once real revenue depends on RDS surviving an AZ outage
  automatically instead of restoring from a backup.
- **The CloudWatch alarm in `logs.tf` notifies nobody yet** - no SNS
  topic/on-call tool exists to wire it to. The alarm firing is real; where
  it pages is a real decision (which on-call tool), not an infra gap.
- **HTTPS isn't wired up** - needs a real, owned domain first (step 3
  above), which this repo doesn't have registered anywhere Terraform
  could see it.
