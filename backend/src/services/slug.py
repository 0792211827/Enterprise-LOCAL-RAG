"""Deterministic slug generation for named domain entities."""
import re


def slugify(value: str) -> str:
    """Return a URL-safe slug for a human-readable name."""
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "item"
