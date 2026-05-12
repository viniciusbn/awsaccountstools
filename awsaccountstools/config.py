"""Configuration management: .env file parsing, validation, and persistence.

Handles two configuration layers:
  - .env        — Template with documented defaults (tracked in git)
  - .env.local  — User-specific overrides with real values (git-ignored)

Values from .env.local override .env. The file also stores last-selection
cache entries (lastAccountId, lastRoleName, etc.) for menu prioritization.
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List

from .ui import msg_error, msg_info, msg_success, msg_warn, prompt_required

# Resolved path to the repository root (parent of this package directory)
APP_DIR = Path(__file__).resolve().parent.parent

# Standard AWS CLI config file path
AWS_CONFIG = Path.home() / ".aws" / "config"

# OAuth scope required for SSO account/role enumeration
DEFAULT_SSO_SCOPES = "sso:account:access"

# Local cache file for dynamically-fetched AWS regions
AWS_REGIONS_FILE = APP_DIR / ".aws_regions"

# Legacy keys that are automatically removed during .env.local updates
_DEPRECATED_ENV_KEYS = {"lastClusterRegion", "lastClusterProfile"}


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a .env-style file into a key-value dictionary.

    Supports # comments, optional quoting (single or double), and
    key=value pairs. Lines without '=' are silently ignored.
    """
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        values[key] = val
    return values


def load_env_config() -> Dict[str, str]:
    """Load merged configuration from .env (defaults) and .env.local (overrides)."""
    data: Dict[str, str] = {}
    data.update(parse_env_file(APP_DIR / ".env"))
    data.update(parse_env_file(APP_DIR / ".env.local"))
    return data


def _env_local_path() -> Path:
    return APP_DIR / ".env.local"


def _write_env_local(
    start_url: str, session: str, region: str, company: str,
) -> None:
    """Write the core configuration keys to .env.local with 0600 permissions."""
    env_local = _env_local_path()
    env_local.write_text(
        "\n".join([
            "# Local runtime configuration. Keep this file out of version control.",
            f'awsStartURL="{start_url}"',
            f'awsDefaultSession="{session}"',
            f'awsDefaultRegion="{region}"',
            f'awsCompanyName="{company}"',
            "",
        ]),
        encoding="utf-8",
    )
    os.chmod(env_local, 0o600)


def save_last_selection(values: Dict[str, str]) -> None:
    """Persist last-used account/role/region/cluster into .env.local.

    Performs an in-place update: existing keys are overwritten, new keys are
    appended under a '# Last selection cache' section. Deprecated keys are
    automatically stripped during the process.
    """
    env_local = _env_local_path()
    env_local.parent.mkdir(parents=True, exist_ok=True)

    if env_local.exists():
        lines = env_local.read_text(encoding="utf-8").splitlines()
    else:
        lines = ["# Local runtime configuration. Keep this file out of version control."]

    keys_to_set = {k: v for k, v in values.items() if v is not None and str(v).strip() != ""}
    if not keys_to_set:
        return

    seen: Dict[str, bool] = {k: False for k in keys_to_set}
    out: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in _DEPRECATED_ENV_KEYS:
            continue
        if key in keys_to_set:
            out.append(f'{key}="{keys_to_set[key]}"')
            seen[key] = True
        else:
            out.append(line)

    missing = [k for k, done in seen.items() if not done]
    if missing:
        if out and out[-1].strip() != "":
            out.append("")
        if not any("Last selection cache" in ln for ln in out):
            out.append("# Last selection cache")
        for key in missing:
            out.append(f'{key}="{keys_to_set[key]}"')

    env_local.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    os.chmod(env_local, 0o600)


def aws_env_without_profile() -> Dict[str, str]:
    """Return a copy of os.environ with AWS profile variables removed.

    Used when invoking AWS CLI commands that should not inherit the
    currently-active profile from the shell session (e.g., SSO login,
    credential export), preventing circular profile references.
    """
    env = os.environ.copy()
    env.pop("AWS_PROFILE", None)
    env.pop("AWS_DEFAULT_PROFILE", None)
    env.pop("PROFILE", None)
    return env


def check_required_config(cfg: Dict[str, str]) -> None:
    """Validate that all required configuration keys are present and non-empty.

    Raises RuntimeError with a descriptive message listing missing keys.
    """
    required = ["awsStartURL", "awsDefaultSession", "awsDefaultRegion"]
    missing = [key for key in required if not cfg.get(key)]
    if missing:
        raise RuntimeError("Missing required configuration: " + ", ".join(missing))


def ensure_env_local(cfg: Dict[str, str]) -> Dict[str, str]:
    """Ensure .env.local exists, prompting the user to create it if missing.

    If running non-interactively (piped stdin), raises RuntimeError instead.
    Returns the fully-merged configuration after creation.
    """
    env_local = _env_local_path()
    if env_local.exists():
        return load_env_config()
    if not sys.stdin.isatty():
        msg_error("No .env.local found. Open an interactive shell to create it automatically.")
        raise RuntimeError("missing .env.local")

    msg_warn("No .env.local found. Creating it now...")
    start_url = prompt_required("awsStartURL", cfg.get("awsStartURL", ""))
    session = prompt_required("awsDefaultSession", cfg.get("awsDefaultSession", ""))
    region = prompt_required("awsDefaultRegion", cfg.get("awsDefaultRegion", "us-east-1"))
    company = prompt_required("awsCompanyName", cfg.get("awsCompanyName", "My Company"))
    _write_env_local(start_url, session, region, company)
    msg_success("Created .env.local successfully.")
    return load_env_config()


def prompt_config_values(
    cfg: Dict[str, str], require_interactive: bool = False,
) -> Dict[str, str]:
    """Interactively prompt the user to review and update all config values.

    Called by the 'configure' command. Rewrites .env.local with the
    confirmed values and returns the refreshed configuration.
    """
    if not sys.stdin.isatty():
        if require_interactive:
            raise RuntimeError("configure requires an interactive shell to edit values")
        return cfg

    msg_info("Review and confirm your configuration values.")
    start_url = prompt_required("awsStartURL", cfg.get("awsStartURL", ""))
    session = prompt_required("awsDefaultSession", cfg.get("awsDefaultSession", ""))
    region = prompt_required("awsDefaultRegion", cfg.get("awsDefaultRegion", "us-east-1"))
    company = prompt_required("awsCompanyName", cfg.get("awsCompanyName", "My Company"))
    _write_env_local(start_url, session, region, company)
    msg_success("Environment configuration saved to .env.local.")
    return load_env_config()


def require_aws_cli() -> bool:
    """Check that the AWS CLI binary is available in PATH."""
    if shutil.which("aws") is None:
        msg_error("AWS CLI is not installed and is required.")
        return False
    return True


def ensure_aws_config_file() -> None:
    """Create ~/.aws/config and its parent directory if they don't exist."""
    AWS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    AWS_CONFIG.touch(exist_ok=True)
