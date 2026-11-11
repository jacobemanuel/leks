"""Ingest worker: read a batch, validate, write checkpoints (toy example code)."""
import json
import os

CHECKPOINT_EVERY = 5000


def read_batch(path):
    # one json record per line
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def validate_record(rec):
    """Return an error code or None when the record is fine."""
    if "id" not in rec:
        return "missing_id"
    if "source_id" not in rec:
        return "missing_source"
    if not isinstance(rec.get("value"), (int, float)):
        return "bad_value"
    return None


def write_checkpoint(path, count):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(count))
    os.replace(tmp, path)  # rename keeps the old file intact on a crash


class DeadLetterQueue:
    """Keeps records that failed validation, with the reason."""

    def __init__(self):
        self.rows = []

    def add(self, rec, code):
        self.rows.append({"record": rec, "error": code})

    def flush(self, path):
        with open(path, "a", encoding="utf-8") as f:
            for row in self.rows:
                f.write(json.dumps(row) + "\n")
        self.rows = []
