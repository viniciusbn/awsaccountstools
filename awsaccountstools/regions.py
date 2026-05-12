"""AWS region management: dynamic fetching, local caching, validation, and selection.

Region data is fetched from the AWS EC2 API and cached locally in .aws_regions
(a JSON array). This avoids repeated API calls and allows offline validation.
The cache is refreshed automatically during profile refresh operations.
"""

import json
import re
import subprocess
from typing import Dict, List, Optional, Tuple

from .config import AWS_REGIONS_FILE, aws_env_without_profile
from .ui import choose_menu, msg_warn, prompt_region_custom

# In-memory cache of regions fetched per profile (avoids redundant API calls)
_region_cache_by_profile: Dict[str, List[str]] = {}

# Ensures the .aws_regions parse warning is shown only once per session
_regions_load_warned = False


def fetch_aws_regions(cfg: Dict[str, str]) -> list:
    """Fetch all AWS region names from EC2 API. Returns a sorted list, or [] on failure."""
    try:
        region = cfg.get("awsDefaultRegion") or "us-east-1"
        proc = subprocess.run(
            [
                "aws", "ec2", "describe-regions",
                "--all-regions",
                "--query", "Regions[].RegionName",
                "--output", "json",
                "--region", region,
            ],
            text=True, capture_output=True, env=aws_env_without_profile(),
        )
        if proc.returncode != 0:
            return []
        regions = json.loads(proc.stdout)
        return sorted({str(r).strip() for r in regions if str(r).strip()})
    except Exception:
        return []


def save_aws_regions(regions: list) -> None:
    """Write the region list to the .aws_regions cache file as JSON."""
    AWS_REGIONS_FILE.write_text(json.dumps(regions, indent=2) + "\n", encoding="utf-8")


def load_aws_regions() -> list:
    """Load regions from the .aws_regions cache file. Returns [] if missing or corrupt."""
    global _regions_load_warned
    if AWS_REGIONS_FILE.exists():
        try:
            data = json.loads(AWS_REGIONS_FILE.read_text(encoding="utf-8"))
            return [str(r).strip() for r in data if str(r).strip()]
        except Exception:
            if not _regions_load_warned:
                msg_warn(
                    "Could not parse .aws_regions cache file. "
                    "Run Refresh/Reconfigure Profiles to rebuild it."
                )
                _regions_load_warned = True
    return []


def _is_region_name_format_valid(region: str) -> bool:
    """Check if a string matches the AWS region name pattern (e.g., us-east-1)."""
    return bool(re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d", region.strip()))


def _load_regions_for_profile(profile: str, fallback_region: str) -> List[str]:
    """Fetch regions via EC2 API using a specific profile (with in-memory caching)."""
    cached = _region_cache_by_profile.get(profile)
    if cached is not None:
        return cached

    proc = subprocess.run(
        [
            "aws", "ec2", "describe-regions",
            "--all-regions",
            "--profile", profile,
            "--region", fallback_region,
            "--query", "Regions[].RegionName",
            "--output", "json",
        ],
        text=True, capture_output=True, env=aws_env_without_profile(),
    )
    if proc.returncode != 0:
        return []
    try:
        raw = json.loads(proc.stdout)
    except Exception:
        return []

    regions = sorted({str(r).strip() for r in raw if str(r).strip()})
    _region_cache_by_profile[profile] = regions
    return regions


def validate_region_exists(
    region: str, profile: str, default_region: str,
) -> Tuple[bool, str]:
    """Validate a region name against the cached region list (fail-closed).

    Tries the local .aws_regions cache first (instant), then falls back
    to the EC2 API. Returns (True, '') on success or (False, error_message)
    on failure. If no region list can be obtained, validation fails.
    """
    candidate = region.strip()
    if not candidate:
        return False, "Region is required."
    if not _is_region_name_format_valid(candidate):
        return False, "Invalid region format. Example: us-east-1"

    # Try cached file first (instant), then fall back to API
    known = load_aws_regions()
    if not known:
        known = _load_regions_for_profile(profile, default_region)

    if known:
        if candidate not in known:
            return False, f"Region '{candidate}' does not exist."
        return True, ""

    return (
        False,
        "Could not validate region list right now. "
        "Run Refresh/Reconfigure Profiles and try again.",
    )


def select_session_region(
    cfg: Dict[str, str], profile: str,
) -> Optional[Tuple[str, bool]]:
    """Interactive menu to choose a region for the current session.

    Options: use default, use last-selected, or type a custom region.
    Returns (region, is_custom) or None on cancel.
    """
    default_region = (cfg.get("awsDefaultRegion") or "us-east-1").strip()
    last_region = (cfg.get("lastRegion") or "").strip()

    options = [f"Use default region: {default_region}"]
    if last_region and last_region != default_region:
        options.append(f"Use last region: {last_region}")
    options.extend(["Type custom region", "Cancel"])

    choice = choose_menu("Select AWS region for this session:", options)
    if choice is None or choice == "Cancel":
        return None
    if choice.startswith("Use default region:"):
        return default_region, False
    if choice.startswith("Use last region:"):
        return last_region, True
    if choice == "Type custom region":
        validator = lambda r: validate_region_exists(r, profile, default_region)
        custom = prompt_region_custom(default_region, validator)
        if not custom:
            return None
        return custom, True
    return default_region, False
