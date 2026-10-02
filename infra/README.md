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

3. **Point the domain at it.** `terraform output alb_dns_name` gives the ALB's
   DNS name. `lmxit.com`'s DNS is at **Cloudflare** (`keyla`/`maciej.
   ns.cloudflare.com`); the registrar is Squarespace, which needs nothing, and
   Google holds only Workspace **email** - `MX` at `aspmx.l.google.com`, an SPF
   include and a site-verification `TXT`. Add three CNAMEs in the Cloudflare
   zone, entering `api` / `ops` / `portal` rather than the full hostname, since
   Cloudflare appends the zone itself. Then request an ACM certificate in
   `us-east-1` for the three names and apply it:
   ```bash
   terraform apply -var certificate_arn=arn:aws:acm:us-east-1:...
   ```
   That one variable creates the HTTPS listener, moves all three host rules
   onto it, and turns `:80` into a 301. **No file needs editing** - this used
   to say "add an HTTPS listener to `infra/aws/alb.tf`", which meant writing
   Terraform against a live stack at the exact moment it is half-built and
   nothing works yet. The certificate still cannot be requested here, because
   the ARN cannot exist before the domain does.

   > **Grey cloud, not orange.** Cloudflare defaults a new CNAME to *Proxied*,
   > and the apex and `www` already are, so it looks like the house style. Set
   > all three - and the ACM validation records - to **DNS only**. Proxied,
   > Cloudflare answers with its own IPs and terminates TLS itself: ACM
   > validation then never completes, and fails by sitting on "Pending
   > validation" indefinitely rather than erroring. If Cloudflare's SSL mode is
   > *Flexible* you also get an infinite redirect loop against an ALB that
   > redirects to HTTPS. The proxy can be turned on deliberately later, with
   > SSL mode **Full (strict)**, once the certificate exists.

   > **Do not move DNS to Route 53 to let Terraform manage it.** That means
   > repointing the nameservers, and every record not recreated in Route 53
   > first stops existing when it propagates - including the `MX`. Company
   > email goes down and stays down until somebody notices. Three subdomains
   > are not worth that.

   > **This step is not optional-later, it is a prerequisite for anything
   > working.** The ALB ships with an HTTP:80 listener only, while the
   > dashboard and portal containers are built with
   > `API_BASE_URL=https://api.lmxit.com` and the API is given
   > `DASHBOARD_CORS_ORIGINS=https://ops.lmxit.com,https://portal.lmxit.com`.
   > So until the certificate and HTTPS listener exist, both front ends load
   > over `http://` and then fail every single request - once because there is
   > nothing listening on 443, and again because an `http://` origin is not in
   > the allow-list even if there were.
   >
   > The failure looks like a broken deployment rather than a missing step,
   > which is the expensive way to discover it. Do DNS, ACM and the listener in
   > one sitting, and only then look at whether the stack works.

4. **Wire up CI/CD**:
   - `terraform output github_actions_deploy_role_arn` → set as the
     `AWS_DEPLOY_ROLE_ARN` repository variable (Settings → Secrets and
     variables → Actions → Variables).
   - Push to `main` - `.github/workflows/deploy.yml` builds, pushes, and
     deploys all three images automatically once CI passes.

   Until that variable is set the deploy job **skips** rather than failing.
   It used to run and fail on every merge, which is how a branch ends up
   permanently part-red and everybody stops reading the ticks.

5. **Run the first migration** (this stack deliberately doesn't run
   migrations automatically on every task start - see `ecs.tf`'s comment
   on why):
   ```bash
   aws ecs run-task --cluster lmx-prod-cluster --task-definition lmx-prod-app \
     --launch-type FARGATE --network-configuration '...' \
     --overrides '{"containerOverrides":[{"name":"app","command":["alembic","upgrade","head"]}]}'
   ```
   The two values it needs are outputs, as of the `ecs_task_subnet_ids` /
   `app_security_group_id` change - they were not, and this instruction could
   not be followed as written:
   ```bash
   SUBNETS=$(terraform output -json ecs_task_subnet_ids | jq -r 'join(",")')
   SG=$(terraform output -raw app_security_group_id)

   aws ecs run-task --cluster lmx-prod-cluster --task-definition lmx-prod-app \
     --launch-type FARGATE \
     --network-configuration \
       "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=ENABLED}" \
     --overrides '{"containerOverrides":[{"name":"app","command":["alembic","upgrade","head"]}]}'
   ```

   > **`assignPublicIp=ENABLED` is not optional.** There is no NAT Gateway -
   > `vpc.tf` argues for that tradeoff - so tasks sit in public subnets with
   > public IPs and a security group that accepts nothing inbound. Omit the
   > flag and the task cannot reach ECR to pull its own image, and fails with a
   > timeout that reads like a networking fault rather than a missing argument.

6. **Create the first ops user.** Nothing else in this runbook mentions it and
   the deployment is unusable without it: there is no self-service signup for
   internal staff by design (`docs/ROADMAP.md` S1), so on a fresh stack **no
   account exists and nobody can log in to `ops.lmxit.com` at all** - a failure
   indistinguishable from a wrong password. Same one-off `run-task` shape:
   ```bash
   aws ecs run-task --cluster lmx-prod-cluster --task-definition lmx-prod-app \
     --launch-type FARGATE \
     --network-configuration \
       "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=ENABLED}" \
     --overrides '{"containerOverrides":[{"name":"app","command":[
        "python","-m","scripts.create_ops_user",
        "--email","you@lmxit.com","--password","<a long one>",
        "--name","Your Name","--role","admin"]}]}'
   ```
   Re-runnable: for an existing email it resets the password and reactivates
   the account rather than erroring, so it doubles as a password reset.
   Omitting `--role` on a re-run leaves the existing role alone, so a reset
   never silently demotes an admin.

   Then create a hub from the console. A hub is the root of everything -
   clients, drivers, routes and closures all hang off one, and public client
   signup is refused outright when none exists.

7. **Fill in real third-party credentials** once each account exists
   (`docs/ROADMAP.md` B4/B5, E1): `aws secretsmanager put-secret-value
   --secret-id $(terraform output -raw secrets_manager_secret_arn) ...`
   with the updated JSON blob. `secrets.tf`'s `ignore_changes` means a
   future `terraform apply` won't reset these back to placeholders.

   **Then redeploy** (`aws ecs update-service --cluster lmx-prod-cluster
   --service lmx-prod-app --force-new-deployment`). The app reads the secret
   once, when its config loads, so a running task never sees a new value.

   Three settings have no placeholder, because an empty value would fail to
   parse and stop the app booting: `EXPO_PUSH_ENABLED` (true or false),
   `SMTP_PORT` and `SMTP_USE_TLS`. Add them with the rest of the SMTP block
   when a provider is chosen, and `GEOCODER_PROVIDER=google` once
   `GOOGLE_MAPS_API_KEY` is in - the key alone switches nothing.
   `INTERNAL_API_TOKEN` needs nothing: Terraform generates it, and the same
   value reaches the scheduled sweeps in `schedules.tf`.

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
- **HTTPS is wired up but unarmed.** The listener, the redirect and the rule
  switching all exist and are validated; what is missing is a certificate ARN
  to put in `var.certificate_arn`, which needs the domain proved first (step 3
  above). The DNS itself stays at Cloudflare and outside Terraform's sight on
  purpose - moving it to Route 53 would take the company's `MX` records down
  with it. The three `/internal` sweeps in `schedules.tf` arm with it, because
  an EventBridge API destination must be an https URL.
