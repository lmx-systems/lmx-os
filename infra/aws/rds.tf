/*
Managed Postgres, replacing docker-compose.yml's single postgres:16-alpine
container - the exact gap named in docs/ROADMAP.md S3 ("no managed
database... no automated backups"). Master password is generated here and
never appears in a variable, a .tf file, or a CI log - it's written
straight into the Secrets Manager secret this stack also creates
(secrets.tf), which is exactly the loop app/secrets_provider.py's
AWSSecretsManagerProvider already exists to close on the app side.
*/

resource "aws_db_subnet_group" "main" {
  name       = "${var.name_prefix}-db"
  subnet_ids = aws_subnet.isolated[*].id
  tags       = { Name = "${var.name_prefix}-db" }
}

resource "random_password" "db_master" {
  length  = 32
  special = false # simplifies embedding in a DATABASE_URL connection string - length carries the entropy
}

resource "aws_db_instance" "main" {
  identifier     = "${var.name_prefix}-db"
  engine         = "postgres"
  engine_version = "16"

  instance_class        = var.db_instance_class
  allocated_storage     = 20
  max_allocated_storage = 100 # storage autoscaling - the one "no autoscaling" gap this file alone closes
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "lmx_os"
  username = "lmx"
  password = random_password.db_master.result
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false

  multi_az                  = var.db_multi_az
  backup_retention_period   = var.db_backup_retention_days
  backup_window             = "07:00-08:00" # UTC - low-traffic window for a single US hub
  maintenance_window        = "sun:08:30-sun:09:30"
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.name_prefix}-db-final"

  tags = { Name = "${var.name_prefix}-db" }
}
