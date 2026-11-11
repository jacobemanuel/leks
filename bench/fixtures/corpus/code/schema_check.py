"""Record schema checks and the version 1 to 2 migration (toy example code)."""

SCHEMA_VERSION = 2


def required_fields(version):
    base = ["id", "value"]
    if version >= 2:
        base.append("source_id")
    return base


def check_schema_version(rec):
    """Return the version a record claims, default 1."""
    v = rec.get("schema", 1)
    if v not in (1, SCHEMA_VERSION):
        raise ValueError("unknown schema version")
    return v


def migrate_record_v1_to_v2(rec):
    out = dict(rec)
    out.setdefault("source_id", "unknown")
    out["schema"] = 2
    return out
