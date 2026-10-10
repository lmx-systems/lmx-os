/*
This is the secret app/secrets_provider.py's AWSSecretsManagerProvider was
built to read - SECRETS_MANAGER_SECRET_ID (set in ecs.tf's task
definition) points the app at this ARN, and every key below lands in
os.environ before Settings is ever constructed (see that module's
docstring). Nothing else about the app needed to change to make this
real.

Generated here: DATABASE_URL (built from this same config, so it can never
drift from the real RDS endpoint) and three
distinct random JWT secrets (assert_jwt_secrets_are_distinct() in
app/config.py refuses to boot outside development if any two ever
match). Third-party credentials (Rippling, Google Maps, Sentry,
Expo push) are placeholders - empty means the app's own existing
"unconfigured -> stub" fallback applies exactly as it does today, and
`ignore_changes` on the secret version means a real value someone pastes
into the AWS console later survives the next `terraform apply` instead
of being silently reset to "".
*/

resource "random_password" "driver_jwt_secret" {
  length  = 48
  special = false
}

resource "random_password" "client_jwt_secret" {
  length  = 48
  special = false
}

resource "random_password" "ops_jwt_secret" {
  length  = 48
  special = false
}

# The shared secret for /internal/*, which answers 404 without one
# (app/api/internal_routes.py). The same value reaches the request through the
# EventBridge connection in schedules.tf, so neither side is ever pasted by hand.
resource "random_password" "internal_api_token" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name_prefix}-app-secrets"
  recovery_window_in_days = 7 # not zero - a fat-fingered `terraform destroy` shouldn't be instantly unrecoverable
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id

  secret_string = jsonencode({
    DATABASE_URL = "postgresql+asyncpg://${aws_db_instance.main.username}:${random_password.db_master.result}@${aws_db_instance.main.endpoint}/${aws_db_instance.main.db_name}"

    DRIVER_JWT_SECRET = random_password.driver_jwt_secret.result
    CLIENT_JWT_SECRET = random_password.client_jwt_secret.result
    OPS_JWT_SECRET    = random_password.ops_jwt_secret.result

    # Generated, like the JWT secrets. `ignore_changes` below covers the whole
    # JSON, so a key added here lands only on the first apply; on a stack that
    # already exists, add it with put-secret-value instead.
    INTERNAL_API_TOKEN = random_password.internal_api_token.result

    # Placeholders - fill these in via the AWS console or `aws
    # secretsmanager put-secret-value` once each real account exists
    # (docs/ROADMAP.md B4, L12, E1). Left empty, every one of these already
    # degrades to this app's existing stub/no-op behavior - see
    # app/messaging/email_client.py, app/payroll/, app/logging_config.py.
    GOOGLE_MAPS_API_KEY     = ""
    GOOGLE_CLOUD_PROJECT_ID = ""
    RIPPLING_API_KEY        = ""
    RIPPLING_BASE_URL       = ""
    SENTRY_DSN              = ""
    EXPO_PUSH_ACCESS_TOKEN  = ""
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}
