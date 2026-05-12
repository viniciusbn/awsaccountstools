"""Pure utility functions with no external or internal dependencies.

All functions here are stateless and side-effect free. They are used across
multiple modules for string manipulation, date parsing, and list ordering.
"""

import datetime as dt
import re
from typing import List, Optional


def sanitize_name(value: str) -> str:
    """Convert an arbitrary string into a safe AWS profile name segment.

    Replaces whitespace and slashes with hyphens, strips special characters,
    and collapses consecutive hyphens. The result contains only alphanumeric
    characters, underscores, dots, and single hyphens.
    """
    safe = []
    prev_dash = False
    for ch in value:
        if ch.isalnum() or ch in "_.-":
            safe.append(ch)
            prev_dash = ch == "-"
        elif ch.isspace() or ch == "/":
            if not prev_dash:
                safe.append("-")
                prev_dash = True
    out = "".join(safe).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out


def build_profile_name(account_name: str, role_name: str) -> str:
    """Build a deterministic AWS CLI profile name from account and role names.

    Format: '<sanitized-account>-<sanitized-role>'
    Example: build_profile_name('My Account', 'AdminRole') → 'My-Account-AdminRole'
    """
    return f"{sanitize_name(account_name)}-{sanitize_name(role_name)}"


def shell_quote(value: str) -> str:
    """Safely quote a value for shell interpolation using single quotes.

    Handles embedded single quotes by breaking out of the single-quoted string,
    adding a double-quoted single quote, and re-entering single quotes.
    """
    return "'" + value.replace("'", "'\"'\"'") + "'"


def parse_iso8601(value: str) -> Optional[dt.datetime]:
    """Parse an ISO 8601 datetime string into a timezone-aware datetime.

    Handles common AWS variants: trailing 'Z', 'UTC' suffix, and missing
    timezone info (defaults to UTC). Returns None on invalid input.
    """
    if not value:
        return None
    fixed = value.strip()
    if fixed.endswith("UTC"):
        fixed = fixed[:-3] + "+00:00"
    else:
        fixed = fixed.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(fixed)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except ValueError:
        return None


def normalize_start_url(value: str) -> str:
    """Normalize an AWS SSO start URL for consistent comparison.

    Strips whitespace, removes trailing slashes, and lowercases the URL.
    This ensures that 'https://example.awsapps.com/start/' and
    'https://example.awsapps.com/start' match correctly.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    return raw.rstrip("/").lower()


def move_preferred_first(items: List[str], preferred: str) -> List[str]:
    """Reorder a list so the preferred item appears first (if present).

    Used to prioritize the last-selected account, role, or cluster
    at the top of interactive menus for faster re-selection.
    """
    if not preferred:
        return list(items)
    out = list(items)
    if preferred in out:
        out.remove(preferred)
        out.insert(0, preferred)
    return out
