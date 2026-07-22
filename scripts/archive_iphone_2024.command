#!/usr/bin/env bash
set -uo pipefail

project_dir="/Users/jamesleakos/Documents/Development/Python/photo-functions"
log_path="/Users/jamesleakos/.photo-manager/iphone-2024.log"
cd "$project_dir"

exec > >(tee -a "$log_path") 2>&1
printf 'iPhone 2024 archive started: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

caffeinate -i .venv/bin/photo-manager iphone-year 2024 \
  --minimum-free-gb 100 \
  --batch-gb 1 \
  --max-batch-items 25
archive_status=$?

printf 'iPhone 2024 archive exited with status %s: %s\n' \
  "$archive_status" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
exit "$archive_status"
