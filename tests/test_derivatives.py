import json
from dataclasses import replace

import boto3

from photo_manager.derivatives import DerivativeDispatcher


class FakeSQS:
    def __init__(self):
        self.messages = []

    def send_message(self, **kwargs):
        self.messages.append(json.loads(kwargs["MessageBody"]))
        return {"MessageId": "one"}

    def send_message_batch(self, **kwargs):
        self.messages.extend(json.loads(entry["MessageBody"]) for entry in kwargs["Entries"])
        return {
            "Successful": [{"Id": entry["Id"]} for entry in kwargs["Entries"]],
            "Failed": [],
        }


def photo(number):
    digest = f"{number:064x}"
    return {
        "sha256": digest,
        "extension": ".arw",
        "object_key": f"photo-manager/originals/{digest[:2]}/{digest}.arw",
    }


def test_derivative_dispatcher_batches_durable_jobs(monkeypatch, settings):
    fake = FakeSQS()
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: fake)
    configured = replace(
        settings,
        storage_backend="s3",
        s3_bucket="archive",
        derivative_queue_url="https://sqs.example/derivatives",
    )
    dispatcher = DerivativeDispatcher(configured)

    queued = dispatcher.enqueue_many(photo(number) for number in range(11))

    assert queued == 11
    assert len(fake.messages) == 11
    assert fake.messages[0]["bucket"] == "archive"
    assert fake.messages[0]["prefix"] == "photo-manager"
    assert fake.messages[-1]["sha256"] == f"{10:064x}"
