#!/usr/bin/env bash
set -euo pipefail

region="${AWS_REGION:-us-west-2}"
profile="${AWS_PROFILE:-default}"
stack_name="${PHOTO_MANAGER_STACK_NAME:-photo-manager-storage}"
monthly_budget="${PHOTO_MANAGER_MONTHLY_BUDGET:-125}"
budget_email="${PHOTO_MANAGER_BUDGET_EMAIL:-}"

account_id="$(aws sts get-caller-identity --profile "$profile" --query Account --output text)"
principal_arn="$(aws sts get-caller-identity --profile "$profile" --query Arn --output text)"
bucket_name="${PHOTO_ARCHIVE_BUCKET:-photo-manager-archive-${account_id}-${region}}"
template_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

aws cloudformation deploy \
  --profile "$profile" \
  --region "$region" \
  --stack-name "$stack_name" \
  --template-file "$template_dir/storage.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    "ArchiveBucketName=$bucket_name" \
    "BootstrapPrincipalArn=$principal_arn" \
    "MonthlyBudget=$monthly_budget" \
    "BudgetEmail=$budget_email"

aws cloudformation describe-stacks \
  --profile "$profile" \
  --region "$region" \
  --stack-name "$stack_name" \
  --query 'Stacks[0].Outputs' \
  --output table
