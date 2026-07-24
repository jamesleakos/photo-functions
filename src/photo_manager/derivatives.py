from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from .config import Settings


@dataclass(frozen=True)
class DerivativeJob:
    bucket: str
    original_key: str
    sha256: str
    extension: str
    prefix: str

    @classmethod
    def from_photo(cls, settings: Settings, photo: dict) -> "DerivativeJob":
        if not settings.s3_bucket:
            raise ValueError("An S3 bucket is required for derivative generation")
        if not photo.get("object_key"):
            raise ValueError("The photo does not have a backed-up original")
        return cls(
            bucket=settings.s3_bucket,
            original_key=photo["object_key"],
            sha256=photo["sha256"],
            extension=photo["extension"],
            prefix=settings.s3_prefix,
        )

    def message_body(self) -> str:
        return json.dumps(
            {
                "version": 1,
                "bucket": self.bucket,
                "original_key": self.original_key,
                "sha256": self.sha256,
                "extension": self.extension,
                "prefix": self.prefix,
            },
            separators=(",", ":"),
            sort_keys=True,
        )


class DerivativeDispatcher:
    """Send durable, idempotent derivative jobs to the AWS worker queue."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.queue_url = settings.derivative_queue_url
        self.client = None
        if self.queue_url:
            import boto3

            self.client = boto3.client("sqs", region_name=settings.s3_region)

    @property
    def enabled(self) -> bool:
        return bool(self.queue_url and self.client)

    def enqueue(self, photo: dict) -> bool:
        if not self.enabled:
            return False
        job = DerivativeJob.from_photo(self.settings, photo)
        self.client.send_message(QueueUrl=self.queue_url, MessageBody=job.message_body())
        return True

    def enqueue_many(self, photos: Iterable[dict]) -> int:
        if not self.enabled:
            return 0
        jobs = [DerivativeJob.from_photo(self.settings, photo) for photo in photos]
        queued = 0
        for offset in range(0, len(jobs), 10):
            batch = jobs[offset : offset + 10]
            response = self.client.send_message_batch(
                QueueUrl=self.queue_url,
                Entries=[
                    {"Id": str(index), "MessageBody": job.message_body()}
                    for index, job in enumerate(batch)
                ],
            )
            failed = response.get("Failed", [])
            if failed:
                reasons = ", ".join(item.get("Message", item["Id"]) for item in failed)
                raise RuntimeError(f"Could not queue derivative jobs: {reasons}")
            queued += len(response.get("Successful", []))
        return queued
