"""AWS SSO authentication, profile management, and API interactions.

Handles the complete SSO lifecycle: reading cached tokens from ~/.aws/sso/cache,
triggering browser-based login when tokens expire, enumerating accessible
accounts and roles, and creating AWS CLI profile sections in ~/.aws/config.

All AWS CLI calls use a sanitized environment (no inherited AWS_PROFILE)
to prevent circular credential resolution.
"""

import datetime as dt
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set

from .config import (
    AWS_CONFIG,
    DEFAULT_SSO_SCOPES,
    aws_env_without_profile,
    ensure_aws_config_file,
)
from .regions import fetch_aws_regions, save_aws_regions
from .ui import msg_error, msg_info, msg_success, msg_warn, is_ui_active, suspend_ui, resume_ui
from .utils import build_profile_name, normalize_start_url, parse_iso8601

# In-memory caches to avoid redundant API calls within a session.
# Cleared explicitly via clear_account_caches() when user requests refresh.
_accounts_cache: Dict[str, List[Tuple[str, str]]] = {}   # sso_session -> [(id, name)]
_roles_cache: Dict[str, List[str]] = {}                   # account_id -> [role_name]


def clear_account_caches() -> None:
    """Clear the in-memory accounts and roles caches."""
    _accounts_cache.clear()
    _roles_cache.clear()


def load_sso_cache_entries(start_url: str) -> List[Dict]:
    """Scan ~/.aws/sso/cache for SSO tokens matching the given start URL.

    Compares URLs case-insensitively with trailing slashes stripped.
    Returns entries sorted by expiration (newest first).
    """
    cache_dir = Path.home() / ".aws" / "sso" / "cache"
    if not cache_dir.exists():
        return []

    target_url = normalize_start_url(start_url)
    entries: List[Dict] = []
    for fp in cache_dir.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        token = data.get("accessToken")
        url = normalize_start_url(data.get("startUrl") or data.get("startURL") or "")
        expires = parse_iso8601(data.get("expiresAt", ""))
        if token and url and target_url and url == target_url and expires:
            entries.append({"token": token, "expires": expires})

    entries.sort(key=lambda e: e["expires"], reverse=True)
    return entries


def get_sso_access_token(cfg: Dict[str, str]) -> Optional[str]:
    """Get the most recent SSO access token for the configured start URL."""
    entries = load_sso_cache_entries(cfg["awsStartURL"])
    if not entries:
        return None
    return entries[0]["token"]


def is_sso_token_valid(cfg: Dict[str, str]) -> bool:
    """Check if the current SSO token exists and has not expired yet."""
    entries = load_sso_cache_entries(cfg["awsStartURL"])
    if not entries:
        return False
    return entries[0]["expires"] > dt.datetime.now(dt.timezone.utc)


def run_aws_json(args: List[str]) -> Dict:
    """Run an AWS CLI command and return the parsed JSON output.

    Uses a clean environment (no AWS_PROFILE) to avoid inheriting
    the active shell profile. Raises RuntimeError on non-zero exit.
    """
    proc = subprocess.run(
        ["aws", *args], text=True, capture_output=True, env=aws_env_without_profile(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "AWS command failed")
    return json.loads(proc.stdout)


def configure_first_connect(cfg: Dict[str, str]) -> None:
    """Write the [sso-session] section to ~/.aws/config if not already present."""
    ensure_aws_config_file()
    section = f"[sso-session {cfg['awsDefaultSession']}]"
    content = AWS_CONFIG.read_text(encoding="utf-8") if AWS_CONFIG.exists() else ""
    if section in content:
        return

    append = "\n".join([
        "",
        section,
        f"sso_start_url = {cfg['awsStartURL']}",
        f"sso_region = {cfg['awsDefaultRegion']}",
        f"sso_registration_scopes = {DEFAULT_SSO_SCOPES}",
        "",
    ])
    with AWS_CONFIG.open("a", encoding="utf-8") as f:
        f.write(append)
    msg_success(f"SSO session configured: {cfg['awsDefaultSession']}")


def list_accessible_accounts(cfg: Dict[str, str]) -> List[Tuple[str, str]]:
    """List all AWS accounts accessible via the current SSO token.

    Uses an in-memory cache keyed by SSO session name. Call
    clear_account_caches() to force a fresh fetch.
    Returns a sorted list of (account_id, account_name) tuples.
    """
    cache_key = cfg.get("awsDefaultSession", "")
    if cache_key in _accounts_cache:
        return _accounts_cache[cache_key]

    token = get_sso_access_token(cfg)
    if not token:
        return []
    data = run_aws_json([
        "sso", "list-accounts",
        "--access-token", token,
        "--region", cfg["awsDefaultRegion"],
        "--output", "json",
    ])
    out: List[Tuple[str, str]] = []
    for item in data.get("accountList", []):
        aid = str(item.get("accountId", "")).strip()
        aname = str(item.get("accountName", "")).strip()
        if aid and aname:
            out.append((aid, aname))
    out.sort(key=lambda x: x[1].lower())
    if cache_key:
        _accounts_cache[cache_key] = out
    return out


def list_account_roles(cfg: Dict[str, str], account_id: str) -> List[str]:
    """List the IAM roles available for a specific account via SSO.

    Uses an in-memory cache keyed by account_id. Call
    clear_account_caches() to force a fresh fetch.
    """
    if account_id in _roles_cache:
        return _roles_cache[account_id]

    token = get_sso_access_token(cfg)
    if not token:
        return []
    data = run_aws_json([
        "sso", "list-account-roles",
        "--access-token", token,
        "--account-id", account_id,
        "--region", cfg["awsDefaultRegion"],
        "--output", "json",
    ])
    roles = [str(r.get("roleName", "")).strip() for r in data.get("roleList", [])]
    roles = [r for r in roles if r]
    roles.sort(key=str.lower)
    _roles_cache[account_id] = roles
    return roles


def _read_all_profiles() -> set:
    """Parse ~/.aws/config and return a set of all existing profile names."""
    if not AWS_CONFIG.exists():
        return set()
    content = AWS_CONFIG.read_text(encoding="utf-8")
    return {m.group(1) for m in re.finditer(r"\[profile ([^\]]+)\]", content)}


def _parse_profile_sections() -> Dict[str, Dict[str, str]]:
    """Parse profile sections from ~/.aws/config into a dict.

    Returns:
        {
          "profile-name": {"key": "value", ...},
          ...
        }
    """
    if not AWS_CONFIG.exists():
        return {}

    sections: Dict[str, Dict[str, str]] = {}
    current_profile: Optional[str] = None

    for raw in AWS_CONFIG.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        m = re.fullmatch(r"\[profile\s+([^\]]+)\]", line)
        if m:
            current_profile = m.group(1).strip()
            sections.setdefault(current_profile, {})
            continue

        if current_profile and "=" in line:
            key, val = line.split("=", 1)
            sections[current_profile][key.strip()] = val.strip()

    return sections


def list_other_profiles(managed_sessions: Set[str]) -> List[str]:
    """List profiles that are not managed by this app.

    A profile is considered managed when:
      - It has sso_session, sso_account_id, and sso_role_name, and
      - Its sso_session belongs to one of the configured company sessions.
    """
    profiles = _parse_profile_sections()
    out: List[str] = []

    for profile_name, keys in profiles.items():
        session = keys.get("sso_session", "")
        is_managed = (
            session in managed_sessions
            and "sso_account_id" in keys
            and "sso_role_name" in keys
        )
        if not is_managed:
            out.append(profile_name)

    out.sort(key=str.lower)
    return out


def create_profile_if_missing(
    cfg: Dict[str, str], profile_name: str, account_id: str, role_name: str,
) -> None:
    """Append a [profile ...] section to ~/.aws/config if it doesn't exist yet."""
    ensure_aws_config_file()
    content = AWS_CONFIG.read_text(encoding="utf-8") if AWS_CONFIG.exists() else ""
    if f"[profile {profile_name}]" in content:
        return
    with AWS_CONFIG.open("a", encoding="utf-8") as f:
        f.write(f"\n[profile {profile_name}]\n")
        f.write(f"sso_session = {cfg['awsDefaultSession']}\n")
        f.write(f"sso_account_id = {account_id}\n")
        f.write(f"sso_role_name = {role_name}\n")
        f.write(f"region = {cfg['awsDefaultRegion']}\n")
    msg_info(f"Profile added: {profile_name}")


def create_aws_profiles(cfg: Dict[str, str]) -> bool:
    """Refresh all AWS CLI profiles by enumerating SSO accounts and roles.

    Also updates the .aws_regions cache. Reads existing profiles once in batch
    and appends all new profiles in a single write for performance.
    Clears in-memory caches so that subsequent selections use fresh data.
    """
    clear_account_caches()
    started = dt.datetime.now()
    msg_info("Refreshing AWS account/role profiles from SSO (first run may take longer)...")

    msg_info("Fetching AWS region list...")
    regions = fetch_aws_regions(cfg)
    if regions:
        save_aws_regions(regions)
        msg_success(f"AWS regions updated: {len(regions)} found.")
    else:
        msg_warn("Could not update AWS region list. Using previous cache or fallback.")

    try:
        accounts = list_accessible_accounts(cfg)
    except Exception as exc:
        msg_error(str(exc))
        return False

    if not accounts:
        msg_warn("No AWS accounts available for this SSO session.")
        return False

    # Batch: read existing profiles once
    existing = _read_all_profiles()
    new_blocks: List[str] = []
    processed = 0
    total = len(accounts)

    for account_id, account_name in accounts:
        processed += 1
        msg_info(f"Refreshing {processed}/{total}: {account_name}")
        try:
            roles = list_account_roles(cfg, account_id)
        except Exception as exc:
            msg_warn(f"Could not fetch roles for {account_name}: {exc}")
            continue

        msg_info(f"Found {len(roles)} role(s) in {account_name}")
        for role_name in roles:
            profile = build_profile_name(account_name, role_name)
            if profile not in existing:
                new_blocks.append(
                    f"\n[profile {profile}]\n"
                    f"sso_session = {cfg['awsDefaultSession']}\n"
                    f"sso_account_id = {account_id}\n"
                    f"sso_role_name = {role_name}\n"
                    f"region = {cfg['awsDefaultRegion']}\n"
                )
                existing.add(profile)
                msg_info(f"Profile added: {profile}")

    # Batch write all new profiles at once
    if new_blocks:
        ensure_aws_config_file()
        with AWS_CONFIG.open("a", encoding="utf-8") as f:
            f.writelines(new_blocks)

    sec = int((dt.datetime.now() - started).total_seconds())
    msg_success(
        f"Profile refresh completed in {sec}s. "
        f"Accounts: {processed}/{total}, new profiles: {len(new_blocks)}."
    )
    return True


def ensure_sso_session(cfg: Dict[str, str]) -> bool:
    """Ensure an active SSO session exists, triggering browser login if needed.

    If the token is valid, returns True immediately. Otherwise, runs
    'aws sso login'. Profiles are created on demand (not bulk-refreshed
    on every login) to avoid unnecessary delays.
    """
    ensure_aws_config_file()
    configure_first_connect(cfg)
    if is_sso_token_valid(cfg):
        return True

    msg_warn(f"SSO session '{cfg['awsDefaultSession']}' is expired or missing. Starting login...")
    suspended = is_ui_active() and suspend_ui()
    try:
        proc = subprocess.run(
            ["aws", "sso", "login", "--sso-session", cfg["awsDefaultSession"]],
            text=True,
            env=aws_env_without_profile(),
        )
    finally:
        if suspended:
            resume_ui()
    if proc.returncode != 0:
        msg_error("Could not authenticate to AWS SSO.")
        return False
    clear_account_caches()
    msg_success("SSO login successful.")
    return True
