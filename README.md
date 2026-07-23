# Photo Manager

A catalog-first photo archive for camera originals and iPhone Favorites. It runs as a local browser app or the same Dockerized service on a server, stores originals in local or S3-compatible object storage, detects exact duplicates and likely lower-resolution variants, and tracks editorial flags without modifying source files.

The original one-off scripts remain in `src/` for compatibility. New work lives in the `photo_manager` package and is intentionally non-destructive.

## What works now

- Recursive camera-card and folder ingestion, including JPEG, HEIC, common RAW formats, MOV, and MP4
- Incremental export of iPhone Favorites from macOS Photos through `osxphotos`
- SHA-256 exact deduplication: one catalog asset, any number of known file locations
- Conservative variant matching using perceptual similarity, capture time, filename, and aspect ratio
- High-resolution master recommendation with side-by-side review for ambiguous matches
- Content-addressed, checksum-verified backup to a local archive or any S3-compatible provider
- Consistent catalog snapshot on every backup, protecting editorial decisions too
- Browser gallery, original download, thumbnails, editorial flags, combined filters, uploads, and backup controls
- Optional HTTP Basic authentication and a Docker image for deployment

The app never deletes source photos. Confirmed lower-resolution variants are excluded from future backup, but existing cloud objects are not automatically pruned.

## Install

Python 3.10+ and ExifTool are required. On macOS:

```bash
brew install exiftool
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
photo-manager init
```

Copy `.env.example` to `.env` and replace its paths. The default configuration uses `~/.photo-manager` and local archive storage, so an `.env` file is optional for a first run.

## Everyday workflow

### 1. Import a camera card or folder

The first command only catalogs files. It does not move, rename, or delete them.

```bash
photo-manager scan /Volumes/CAMERA/DCIM --source camera --verbose
```

Use `--dry-run` to count new and byte-identical files before writing the catalog.

### 2. Import iPhone Favorites

Install the optional [osxphotos](https://rhettbull.github.io/osxphotos/) integration and make sure the Mac Photos library is synchronized with iCloud:

```bash
pip install -e '.[iphone]'
photo-manager iphone-favorites
```

This incrementally exports only Favorites, downloads missing iCloud originals when needed, and catalogs them as `iphone-favorite`. The export database is retained so later runs only process additions or updates.

For a low-disk-space yearly archive, use the guarded streaming workflow:

```bash
photo-manager iphone-year 2024 --minimum-free-gb 100 --batch-gb 1
```

It sizes Favorites before download, processes small resumable batches, uploads and verifies each batch, then immediately removes its temporary export. It refuses to start the next batch if the configured free-space reserve cannot be maintained. The estimate allows additional room for edited versions, RAW/Live Photo components, and temporary staging.

### 3. Review and tag

```bash
photo-manager serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Review uncertain variants under **Duplicate review**, then flag photos as **Flagship**, **Include**, **Candidate**, or **One of**. “One of” is a shared shortlist and can be applied to any number of alternatives. The flag, source, favourite, and captured-date filters compose with one another.

### 4. Back up

```bash
photo-manager backup
```

Backup is idempotent. Object keys are derived from SHA-256 content hashes, every upload is verified, and a consistent `catalog-latest.db` snapshot is stored alongside the photos.

## Cloud storage

The storage layer uses the S3 API and supports AWS S3, Cloudflare R2, and Backblaze B2. Keep `PHOTO_S3_STORAGE_CLASS` empty for R2/B2.

```dotenv
PHOTO_STORAGE_BACKEND=s3
PHOTO_S3_BUCKET=my-photo-archive
PHOTO_S3_REGION=auto
PHOTO_S3_ENDPOINT_URL=https://ACCOUNT_ID.r2.cloudflarestorage.com
PHOTO_S3_PREFIX=photo-manager
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

For AWS, omit `PHOTO_S3_ENDPOINT_URL` and set the bucket's AWS region. Enable bucket versioning and block public access at the provider. See [deployment and storage notes](docs/DEPLOYMENT.md) before exposing the service.

## Run locally with Docker

Set `PHOTO_AUTH_USERNAME` and `PHOTO_AUTH_PASSWORD` in `.env`, then:

```bash
docker compose up --build
```

The CLI refuses to bind to a non-loopback address without both credentials. The hosted app uses
these credentials on a normal sign-in page and keeps the browser signed in for 30 days.

## Deploy the hosted gallery

`render.yaml` defines a Render Starter service—the least expensive instance that stays running—
that restores the catalog from S3 and persists every tagging decision back to S3. Set the four
prompted secrets—gallery username/password and the dedicated hosted-gallery AWS access key—and
deploy the Blueprint. Imports and backup controls are disabled in hosted mode; camera and iPhone
ingestion remain on the Mac.

See [deployment and storage notes](docs/DEPLOYMENT.md#render-hosted-gallery) for the IAM boundary,
cold-start behavior, and restore model.

## Duplicate safety model

1. Byte-identical files share one logical asset immediately.
2. High-confidence visual variants are grouped and the highest-quality member becomes the proposed master.
3. Ambiguous matches stay pending; every member remains backup-eligible until reviewed.
4. After confirmation, only the selected master is eligible for new backup.
5. No source or existing backup object is deleted automatically.

This protects intentional crops and edits from being silently discarded while still solving the common full-resolution camera original versus reduced phone export case.

## Development

```bash
pytest -q
ruff check src/photo_manager tests
ruff format --check src/photo_manager tests
```

See [architecture and roadmap](docs/ARCHITECTURE.md) for the catalog model and the next production milestones.

## Legacy tools

The previous scripts still work directly:

```bash
python src/heic_to_jpg.py /path/to/photos
python src/photo_replacer.py /path/to/phone /path/to/camera
python src/photo_merger.py /path/to/source /path/to/target
python src/fix_dates.py /path/to/photos
```

They are destructive or filename-based and are not used by the new application.
