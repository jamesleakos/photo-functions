# Photo Manager AWS storage

`storage.yaml` is the authoritative definition of the Photo Manager archive bucket, local
application role, derivative queues, and storage budget. `deploy.sh` creates or updates the
`photo-manager-storage` CloudFormation stack. `deploy_derivatives.sh` also packages and deploys
the isolated Lambda derivative worker.

## Current deployment

- Region: `us-west-2`
- Stack: `photo-manager-storage`
- Storage class: S3 Intelligent-Tiering
- Budget: $125 USD per month for S3 in `us-west-2`
- Application profile: `photo-manager`, assuming the `photo-manager-local` role
- Derivative worker: `photo-manager-derivative-worker`, two concurrent 2 GB Lambda invocations

The bucket has `DeletionPolicy: Retain`; deleting the stack does not delete the archive. The application role can list, upload, and download archive objects but cannot delete them.

## Deploy image processing

```bash
AWS_PROFILE=default AWS_REGION=us-west-2 ./infra/aws/deploy_derivatives.sh
```

Use the printed queue URL as `PHOTO_DERIVATIVE_QUEUE_URL`, then run
`photo-manager derivatives-backfill` once. New backups enqueue automatically.

## Add budget email notifications

```bash
PHOTO_MANAGER_BUDGET_EMAIL=you@example.com ./infra/aws/deploy.sh
```

The email subscriber receives a forecasted alert at 80% and an actual-cost alert at 100% of the monthly threshold.

## Verify the deployment

```bash
aws sts get-caller-identity --profile photo-manager
aws s3api get-bucket-location \
  --profile photo-manager \
  --bucket photo-manager-archive-784249554271-us-west-2
```

Do not add access keys to the repository. The `photo-manager` profile uses the existing local bootstrap credentials only to assume the restricted application role.
