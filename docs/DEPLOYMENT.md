# Deployment and storage notes

## Choose the role of this service

For a personal library, start with one authoritative instance:

1. Run locally on the Mac if direct camera-card access and iPhone Favorites automation matter most.
2. Deploy the Docker image if access away from home matters most; use browser uploads and a persistent volume for the SQLite catalog/cache.

Do not run separate local and hosted catalogs against the same object prefix. That topology needs the planned local sync agent or a shared PostgreSQL catalog.

## Provider guidance

- Backblaze B2 is a strong default when lowest always-hot storage cost is the priority.
- Cloudflare R2 is attractive when the deployed gallery will download originals often because internet egress is not billed.
- AWS S3 is appropriate when Canadian region placement, IAM integration, or a second deep archive copy matters more than the simplest bill.

Avoid using Glacier Flexible Retrieval or Deep Archive as the primary gallery store: archived originals need a restore operation before the app can serve them. They can be useful as a second, cold copy after restore automation exists.

## AWS storage stack

The repository includes a native CloudFormation stack at `infra/aws/storage.yaml`. It creates:

- a private, encrypted, versioned S3 bucket retained if the stack is deleted;
- a lifecycle transition into S3 Intelligent-Tiering;
- cleanup of incomplete multipart uploads and noncurrent versions;
- a least-privilege local role without object deletion permission; and
- a region- and service-filtered monthly AWS Budget.

Deploy or update it with:

```bash
AWS_PROFILE=default \
AWS_REGION=us-west-2 \
PHOTO_MANAGER_MONTHLY_BUDGET=125 \
PHOTO_MANAGER_BUDGET_EMAIL=you@example.com \
./infra/aws/deploy.sh
```

`PHOTO_MANAGER_BUDGET_EMAIL` is optional. CloudFormation updates the existing stack safely on later runs.

For every provider:

- keep the bucket private;
- enable versioning;
- create credentials scoped to one bucket/prefix;
- configure provider-side object lock or immutability if available;
- enable billing alerts;
- test restore of both an original and `metadata/catalog-latest.db`.

## Docker deployment

Build and run with a persistent volume:

```bash
docker build -t photo-manager .
docker run --rm -p 8000:8000 \
  --env-file .env \
  -v photo-manager-data:/data \
  photo-manager
```

At minimum, a deployed `.env` needs:

```dotenv
PHOTO_DATA_DIR=/data
PHOTO_STORAGE_BACKEND=s3
PHOTO_S3_BUCKET=your-private-bucket
PHOTO_S3_REGION=your-region
PHOTO_S3_ENDPOINT_URL=your-provider-endpoint-or-empty-for-aws
PHOTO_AUTH_USERNAME=your-username
PHOTO_AUTH_PASSWORD=a-long-random-password
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

Put TLS in front of the container. Basic authentication is a deployment floor, not the final multi-user security design.

## Render hosted gallery

The included `render.yaml` creates an authenticated gallery on Render's lowest-cost always-on
Starter web instance in the Oregon region.
Hosted gallery mode is intentionally stateless: it restores `metadata/catalog-latest.db` from
S3 on a cold start, uploads a fresh catalog snapshot after every tag, magazine, or duplicate
decision, and stores generated thumbnails under `photo-manager/thumbnails/`. Browser uploads,
filesystem scans, and backup runs are disabled on the hosted instance.

Set these secret environment variables in Render when the Blueprint is created:

```dotenv
PHOTO_AUTH_USERNAME=your-login-name
PHOTO_AUTH_PASSWORD=a-long-random-password
AWS_ACCESS_KEY_ID=the-hosted-gallery-key
AWS_SECRET_ACCESS_KEY=the-hosted-gallery-secret
```

Use the dedicated `photo-manager-render-gallery` IAM user created by the storage stack. It can
list and read archive objects, and can write only the catalog snapshot and thumbnail cache; it
cannot delete or replace archive originals. Bucket listing lets S3 distinguish a missing cached
thumbnail (404) from a forbidden object (403). Its filesystem is ephemeral by design; S3 remains
the authoritative store.

## Restore

1. Download `photo-manager/metadata/catalog-latest.db` from the bucket.
2. Stop the service.
3. Place the snapshot at the configured `PHOTO_DATABASE_PATH`.
4. Start the service and verify `/health`, gallery counts, and a sample original.

The original media objects use content-addressed keys under `photo-manager/originals/<hash-prefix>/` and can be independently inventoried even if the catalog is unavailable.
