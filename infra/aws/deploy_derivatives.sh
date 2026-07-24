#!/usr/bin/env bash
set -euo pipefail

region="${AWS_REGION:-us-west-2}"
profile="${AWS_PROFILE:-default}"
storage_stack="${PHOTO_MANAGER_STACK_NAME:-photo-manager-storage}"
worker_stack="${PHOTO_MANAGER_DERIVATIVE_STACK_NAME:-photo-manager-derivatives}"
account_id="$(aws sts get-caller-identity --profile "$profile" --query Account --output text)"
bucket_name="${PHOTO_ARCHIVE_BUCKET:-photo-manager-archive-${account_id}-${region}}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build_dir="$(mktemp -d)"
trap 'rm -rf "$build_dir"' EXIT

"$script_dir/deploy.sh"

queue_arn="$(aws cloudformation describe-stacks \
  --profile "$profile" \
  --region "$region" \
  --stack-name "$storage_stack" \
  --query "Stacks[0].Outputs[?OutputKey=='DerivativeQueueArn'].OutputValue" \
  --output text)"

python3 -m pip install \
  --quiet \
  --disable-pip-version-check \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --target "$build_dir/package" \
  "Pillow>=10,<13" \
  "pillow-heif>=0.15,<2" \
  "rawpy>=0.21,<1"

cp "$script_dir/derivative_worker/handler.py" "$build_dir/package/handler.py"
(
  cd "$build_dir/package"
  zip -q -r "$build_dir/derivative-worker.zip" .
)
code_sha="$(shasum -a 256 "$build_dir/derivative-worker.zip" | awk '{print $1}')"
code_key="photo-manager/infrastructure/derivative-worker-${code_sha}.zip"
aws s3 cp \
  "$build_dir/derivative-worker.zip" \
  "s3://${bucket_name}/${code_key}" \
  --profile "$profile" \
  --region "$region" \
  --only-show-errors

aws cloudformation deploy \
  --profile "$profile" \
  --region "$region" \
  --stack-name "$worker_stack" \
  --template-file "$script_dir/derivative-worker.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    "ArchiveBucketName=$bucket_name" \
    "ArchivePrefix=photo-manager" \
    "DerivativeQueueArn=$queue_arn" \
    "LambdaCodeKey=$code_key"

aws cloudformation describe-stacks \
  --profile "$profile" \
  --region "$region" \
  --stack-name "$storage_stack" \
  --query "Stacks[0].Outputs[?OutputKey=='DerivativeQueueUrl'].OutputValue" \
  --output text
