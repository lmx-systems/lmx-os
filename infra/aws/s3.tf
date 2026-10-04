/*
Backs app/storage/photo_upload_client.py's S3PhotoUploadClient -
PHOTO_UPLOAD_BUCKET (set in ecs.tf's task definition) points the app at
this bucket, closing docs/ROADMAP.md A2/A3's real remaining gap. CORS is
scoped to PUT, for the driver app's presigned uploads. Nothing reads the
bucket by its plain object URL - it is private - so every read is a GET
link the API signs per response (readable_url in
app/storage/photo_upload_client.py), opened by an <img> tag or a plain
link, neither of which needs CORS.
*/

resource "aws_s3_bucket" "photo_uploads" {
  bucket = "${var.name_prefix}-photo-uploads"
}

resource "aws_s3_bucket_public_access_block" "photo_uploads" {
  bucket                  = aws_s3_bucket.photo_uploads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "photo_uploads" {
  bucket = aws_s3_bucket.photo_uploads.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_cors_configuration" "photo_uploads" {
  bucket = aws_s3_bucket.photo_uploads.id
  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = ["*"] # a mobile app, not a browser - no Origin header to scope this to
    allowed_headers = ["Content-Type"]
    max_age_seconds = 3000
  }
}

# Proof-of-delivery photos/signatures/barcode scans are business records,
# not disposable - no expiration lifecycle rule here on purpose. Revisit
# only if a real retention-period requirement ever gets specified.
resource "aws_s3_bucket_versioning" "photo_uploads" {
  bucket = aws_s3_bucket.photo_uploads.id
  versioning_configuration {
    status = "Enabled"
  }
}
