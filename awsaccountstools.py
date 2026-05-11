#!/usr/bin/env python3
import atexit
import argparse
import curses
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

APP_DIR = Path(__file__).resolve().parent
AWS_CONFIG = Path.home() / ".aws" / "config"
DEFAULT_SSO_SCOPES = "sso:account:access"

_CURSES_STDSCR = None
_CURSES_TTY_IN = None
_CURSES_TTY_OUT = None
_SAVED_STDIN_FD = None
_SAVED_STDOUT_FD = None
_UI_STATUS_LINE = ""
_UI_COMPANY_NAME = "My Company"
_UI_COMPANY_LOGO = None  # type: Optional[str]
_REGION_CACHE_BY_PROFILE: Dict[str, List[str]] = {}
_AWS_REGIONS_LOAD_WARNED = False
_CP_HEADER = 1
_CP_COMPANY = 2
_CP_SELECTED = 3
_CP_INFO = 4
_CP_WARN = 5
_CP_ERROR = 6
_CP_HINT = 7
_DEPRECATED_ENV_KEYS = {"lastClusterRegion", "lastClusterProfile"}
# --- Dynamic AWS Regions ---
AWS_REGIONS_FILE = APP_DIR / ".aws_regions"

def fetch_aws_regions(cfg: Dict[str, str]) -> list:
    """Fetch AWS regions using AWS CLI and return as a sorted list."""
    try:
        region = cfg.get("awsDefaultRegion") or "us-east-1"
        proc = subprocess.run([
            "aws", "ec2", "describe-regions",
            "--all-regions", "--query", "Regions[].RegionName", "--output", "json", "--region", region
        ], text=True, capture_output=True, env=aws_env_without_profile())
        if proc.returncode != 0:
            return []
        regions = json.loads(proc.stdout)
        return sorted({str(r).strip() for r in regions if str(r).strip()})
    except Exception:
        return []

def save_aws_regions(regions: list) -> None:
    AWS_REGIONS_FILE.write_text(json.dumps(regions, indent=2) + "\n", encoding="utf-8")

def load_aws_regions() -> list:
    global _AWS_REGIONS_LOAD_WARNED
    if AWS_REGIONS_FILE.exists():
        try:
            data = json.loads(AWS_REGIONS_FILE.read_text(encoding="utf-8"))
            return [str(r).strip() for r in data if str(r).strip()]
        except Exception:
            if not _AWS_REGIONS_LOAD_WARNED:
                msg_warn("Could not parse .aws_regions cache file. Run Refresh/Reconfigure Profiles to rebuild it.")
                _AWS_REGIONS_LOAD_WARNED = True
            return []
    return []


def ui_menu_active() -> bool:
    return _CURSES_STDSCR is not None


def set_ui_company_name(company_name: str) -> None:
    global _UI_COMPANY_NAME
    name = (company_name or "").strip()
    _UI_COMPANY_NAME = name or "My Company"

def set_ui_company_logo(logo: Optional[str]) -> None:
    global _UI_COMPANY_LOGO
    if logo and logo.strip():
        _UI_COMPANY_LOGO = logo.strip("\n")
    else:
        _UI_COMPANY_LOGO = None


def _ui_safe_add(stdscr, y: int, x: int, text: str, max_x: int, attr: int = 0) -> None:
    if y < 0:
        return
    truncated = text[: max(0, max_x - x - 1)]
    try:
        stdscr.addstr(y, x, truncated, attr)
    except curses.error:
        pass


def _ui_color(pair_id: int, fallback: int = 0) -> int:
    try:
        if curses.has_colors():
            return curses.color_pair(pair_id)
    except Exception:
        pass
    return fallback


def _ui_status_attr(level: str) -> int:
    upper = level.upper()
    if upper == "ERROR":
        return _ui_color(_CP_ERROR, curses.A_BOLD)
    if upper == "WARN":
        return _ui_color(_CP_WARN, curses.A_BOLD)
    if upper == "OK":
        return _ui_color(_CP_INFO, curses.A_BOLD)
    return _ui_color(_CP_INFO, curses.A_NORMAL)


def _ui_draw_frame(stdscr, title: str) -> int:
    max_y, max_x = stdscr.getmaxyx()
    header_attr = _ui_color(_CP_HEADER, curses.A_BOLD)
    company_attr = _ui_color(_CP_COMPANY, curses.A_BOLD)

    if max_x > 2:
        _ui_safe_add(stdscr, 0, 0, " " * (max_x - 1), max_x, header_attr)
    _ui_safe_add(stdscr, 0, 2, "AWS Accounts Tools", max_x, header_attr | curses.A_BOLD)

    # Show logo if defined, otherwise show company name
    logo_lines = None
    if _UI_COMPANY_LOGO:
        logo_lines = _UI_COMPANY_LOGO.splitlines()
    if logo_lines:
        start_row = 1
        for idx, line in enumerate(logo_lines):
            line = line.rstrip()
            col = max(0, (max_x - len(line)) // 2)
            _ui_safe_add(stdscr, start_row + idx, col, line, max_x, company_attr)
        content_row = start_row + len(logo_lines) + 1
    else:
        content = f" Company: {_UI_COMPANY_NAME} "
        box_inner = min(max(10, len(content)), max(10, max_x - 8))
        box_w = min(max_x - 2, box_inner + 2)
        left = max(0, (max_x - box_w) // 2)
        top = 1
        if max_y >= 5 and box_w >= 6:
            top_border = "+" + "-" * (box_w - 2) + "+"
            middle_text = content[: box_w - 2].ljust(box_w - 2)
            mid_line = f"|{middle_text}|"
            _ui_safe_add(stdscr, top, left, top_border, max_x, company_attr)
            _ui_safe_add(stdscr, top + 1, left, mid_line, max_x, company_attr)
            _ui_safe_add(stdscr, top + 2, left, top_border, max_x, company_attr)
        content_row = 4

    title_attr = _ui_color(_CP_HEADER, curses.A_BOLD) | curses.A_BOLD
    _ui_safe_add(stdscr, content_row, 0, title, max_x, title_attr)
    return content_row + 2


def _ui_flash_center(message: str, seconds: float = 1.0, level: str = "INFO") -> None:
    if _CURSES_STDSCR is None:
        return

    try:
        stdscr = _CURSES_STDSCR
        stdscr.clear()
        max_y, max_x = stdscr.getmaxyx()

        row = max(0, max_y // 2)
        col = max(0, (max_x - len(message)) // 2)
        flash_attr = _ui_status_attr(level) | curses.A_BOLD | curses.A_REVERSE
        _ui_safe_add(stdscr, row, col, message, max_x, flash_attr)
        stdscr.refresh()
        time.sleep(seconds)
    except Exception:
        pass


def _ui_show_status(level: str, message: str) -> None:
    global _UI_STATUS_LINE
    if _CURSES_STDSCR is None:
        return

    _UI_STATUS_LINE = f"{level.upper()} {message}"
    try:
        stdscr = _CURSES_STDSCR
        max_y, max_x = stdscr.getmaxyx()
        stdscr.clear()

        content_row = _ui_draw_frame(stdscr, "AWS Accounts Tools")
        status = _UI_STATUS_LINE
        hint = "Working..."

        try:
            _ui_safe_add(stdscr, content_row, 0, status, max_x, _ui_status_attr(level))
            hint_attr = _ui_color(_CP_HINT, curses.A_DIM) | curses.A_DIM
            _ui_safe_add(stdscr, max(0, max_y - 1), 0, hint, max_x, hint_attr)
        except curses.error:
            pass

        stdscr.refresh()
    except Exception:
        pass


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def parse_env_file(path: Path) -> Dict[str, str]:
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
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        values[key] = val
    return values


def choose_env_file() -> Path:
    env_local = APP_DIR / ".env.local"
    if env_local.exists():
        return env_local
    return APP_DIR / ".env"


def load_env_config() -> Dict[str, str]:
    data: Dict[str, str] = {}
    data.update(parse_env_file(APP_DIR / ".env"))
    data.update(parse_env_file(APP_DIR / ".env.local"))
    return data


def _env_local_path() -> Path:
    return APP_DIR / ".env.local"


def _move_preferred_first(items: List[str], preferred: str) -> List[str]:
    if not preferred:
        return list(items)
    out = list(items)
    if preferred in out:
        out.remove(preferred)
        out.insert(0, preferred)
    return out


def save_last_selection(values: Dict[str, str]) -> None:
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
    env = os.environ.copy()
    env.pop("AWS_PROFILE", None)
    env.pop("AWS_DEFAULT_PROFILE", None)
    env.pop("PROFILE", None)
    return env


def _styled_log(level: str, message: str) -> str:
    if not sys.stderr.isatty():
        return f"{level} {message}"

    colors = {
        "INFO": "\033[36m",   # cyan
        "WARN": "\033[33m",   # yellow
        "ERROR": "\033[31m",  # red
        "OK": "\033[32m",     # green
    }
    reset = "\033[0m"
    color = colors.get(level, "")
    return f"{color}{level}{reset} {message}" if color else f"{level} {message}"


def msg_info(message: str) -> None:
    if ui_menu_active():
        _ui_show_status("INFO", message)
        return
    print(_styled_log("INFO", message), file=sys.stderr)


def msg_warn(message: str) -> None:
    if ui_menu_active():
        _ui_show_status("WARN", message)
        return
    print(_styled_log("WARN", message), file=sys.stderr)


def msg_error(message: str) -> None:
    if ui_menu_active():
        _ui_show_status("ERROR", message)
        return
    print(_styled_log("ERROR", message), file=sys.stderr)


def msg_success(message: str) -> None:
    if ui_menu_active():
        _ui_show_status("OK", message)
        return
    print(_styled_log("OK", message), file=sys.stderr)


def require_aws_cli() -> bool:
    if shutil.which("aws") is None:
        msg_error("AWS CLI is not installed and is required.")
        return False
    return True


def prompt_required(name: str, default: str) -> str:
    tty_out = None
    tty_in = None
    try:
        tty_out = open("/dev/tty", "w", buffering=1)
        tty_in = open("/dev/tty", "r")
    except Exception:
        pass

    display = tty_out or sys.stdout
    input_src = tty_in or sys.stdin

    try:
        while True:
            try:
                if tty_in:
                    display.write(f"{name} [{default}]: ")
                    display.flush()
                    typed = input_src.readline().strip()
                else:
                    typed = input(f"{name} [{default}]: ").strip()
                
                if typed:
                    return typed
                if default:
                    return default
                print("This field is required.", file=display)
                display.flush()
            except KeyboardInterrupt:
                print(file=display)
                display.flush()
                msg_warn("Cancelled by user.")
                raise
    finally:
        if tty_out:
            tty_out.close()
        if tty_in:
            tty_in.close()


def ensure_env_local(cfg: Dict[str, str]) -> Dict[str, str]:
    env_local = APP_DIR / ".env.local"
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

    env_local.write_text(
        "\n".join(
            [
                "# Local runtime configuration. Keep this file out of version control.",
                f'awsStartURL="{start_url}"',
                f'awsDefaultSession="{session}"',
                f'awsDefaultRegion="{region}"',
                f'awsCompanyName="{company}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    os.chmod(env_local, 0o600)
    msg_success("Created .env.local successfully.")
    return load_env_config()


def prompt_config_values(cfg: Dict[str, str], require_interactive: bool = False) -> Dict[str, str]:
    if not sys.stdin.isatty():
        if require_interactive:
            raise RuntimeError("configure requires an interactive shell to edit values")
        return cfg

    msg_info("Review and confirm your configuration values.")
    start_url = prompt_required("awsStartURL", cfg.get("awsStartURL", ""))
    session = prompt_required("awsDefaultSession", cfg.get("awsDefaultSession", ""))
    region = prompt_required("awsDefaultRegion", cfg.get("awsDefaultRegion", "us-east-1"))
    company = prompt_required("awsCompanyName", cfg.get("awsCompanyName", "My Company"))

    env_local = APP_DIR / ".env.local"
    env_local.write_text(
        "\n".join(
            [
                "# Local runtime configuration. Keep this file out of version control.",
                f'awsStartURL="{start_url}"',
                f'awsDefaultSession="{session}"',
                f'awsDefaultRegion="{region}"',
                f'awsCompanyName="{company}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    os.chmod(env_local, 0o600)
    msg_success("Environment configuration saved to .env.local.")
    return load_env_config()


def check_required_config(cfg: Dict[str, str]) -> None:
    required = ["awsStartURL", "awsDefaultSession", "awsDefaultRegion"]
    missing = [key for key in required if not cfg.get(key)]
    if missing:
        raise RuntimeError("Missing required configuration: " + ", ".join(missing))


def ensure_aws_config_file() -> None:
    AWS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    AWS_CONFIG.touch(exist_ok=True)


def sanitize_name(value: str) -> str:
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
    return f"{sanitize_name(account_name)}-{sanitize_name(role_name)}"


def parse_iso8601(value: str) -> Optional[dt.datetime]:
    if not value:
        return None
    fixed = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(fixed)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except ValueError:
        return None


def load_sso_cache_entries(start_url: str) -> List[Dict]:
    cache_dir = Path.home() / ".aws" / "sso" / "cache"
    if not cache_dir.exists():
        return []

    entries: List[Dict] = []
    for fp in cache_dir.glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        token = data.get("accessToken")
        url = data.get("startUrl") or data.get("startURL")
        expires = parse_iso8601(data.get("expiresAt", ""))
        if token and url == start_url and expires:
            entries.append({"token": token, "expires": expires})

    entries.sort(key=lambda e: e["expires"], reverse=True)
    return entries


def get_sso_access_token(cfg: Dict[str, str]) -> Optional[str]:
    entries = load_sso_cache_entries(cfg["awsStartURL"])
    if not entries:
        return None
    return entries[0]["token"]


def is_sso_token_valid(cfg: Dict[str, str]) -> bool:
    entries = load_sso_cache_entries(cfg["awsStartURL"])
    if not entries:
        return False
    return entries[0]["expires"] > dt.datetime.now(dt.timezone.utc)


def run_aws_json(args: List[str]) -> Dict:
    proc = subprocess.run(["aws", *args], text=True, capture_output=True, env=aws_env_without_profile())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "AWS command failed")
    return json.loads(proc.stdout)


def configure_first_connect(cfg: Dict[str, str]) -> None:
    ensure_aws_config_file()
    section = f"[sso-session {cfg['awsDefaultSession']}]"
    content = AWS_CONFIG.read_text(encoding="utf-8") if AWS_CONFIG.exists() else ""
    if section in content:
        return

    append = "\n".join(
        [
            "",
            section,
            f"sso_start_url = {cfg['awsStartURL']}",
            f"sso_region = {cfg['awsDefaultRegion']}",
            f"sso_registration_scopes = {DEFAULT_SSO_SCOPES}",
            "",
        ]
    )
    with AWS_CONFIG.open("a", encoding="utf-8") as f:
        f.write(append)
    msg_success(f"SSO session configured: {cfg['awsDefaultSession']}")


def list_accessible_accounts(cfg: Dict[str, str]) -> List[Tuple[str, str]]:
    token = get_sso_access_token(cfg)
    if not token:
        return []

    data = run_aws_json(
        [
            "sso",
            "list-accounts",
            "--access-token",
            token,
            "--region",
            cfg["awsDefaultRegion"],
            "--output",
            "json",
        ]
    )

    out: List[Tuple[str, str]] = []
    for item in data.get("accountList", []):
        aid = str(item.get("accountId", "")).strip()
        aname = str(item.get("accountName", "")).strip()
        if aid and aname:
            out.append((aid, aname))

    out.sort(key=lambda x: x[1].lower())
    return out


def list_account_roles(cfg: Dict[str, str], account_id: str) -> List[str]:
    token = get_sso_access_token(cfg)
    if not token:
        return []

    data = run_aws_json(
        [
            "sso",
            "list-account-roles",
            "--access-token",
            token,
            "--account-id",
            account_id,
            "--region",
            cfg["awsDefaultRegion"],
            "--output",
            "json",
        ]
    )

    roles = [str(r.get("roleName", "")).strip() for r in data.get("roleList", [])]
    roles = [r for r in roles if r]
    roles.sort(key=str.lower)
    return roles


def profile_exists(profile_name: str) -> bool:
    if not AWS_CONFIG.exists():
        return False
    return f"[profile {profile_name}]" in AWS_CONFIG.read_text(encoding="utf-8")


def create_profile_if_missing(cfg: Dict[str, str], profile_name: str, account_id: str, role_name: str) -> None:
    ensure_aws_config_file()
    if profile_exists(profile_name):
        return

    with AWS_CONFIG.open("a", encoding="utf-8") as f:
        f.write("\n")
        f.write(f"[profile {profile_name}]\n")
        f.write(f"sso_session = {cfg['awsDefaultSession']}\n")
        f.write(f"sso_account_id = {account_id}\n")
        f.write(f"sso_role_name = {role_name}\n")
        f.write(f"region = {cfg['awsDefaultRegion']}\n")
    msg_info(f"Profile added: {profile_name}")


def create_aws_profiles(cfg: Dict[str, str]) -> bool:
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

    created_profiles = 0
    processed_accounts = 0
    total_accounts = len(accounts)

    for account_id, account_name in accounts:
        processed_accounts += 1
        msg_info(f"Refreshing {processed_accounts}/{total_accounts}: {account_name}")
        try:
            roles = list_account_roles(cfg, account_id)
        except Exception as exc:
            msg_warn(f"Could not fetch roles for {account_name}: {exc}")
            continue

        msg_info(f"Found {len(roles)} role(s) in {account_name}")
        for role_name in roles:
            profile = build_profile_name(account_name, role_name)
            before = profile_exists(profile)
            create_profile_if_missing(cfg, profile, account_id, role_name)
            if not before:
                created_profiles += 1

    sec = int((dt.datetime.now() - started).total_seconds())
    msg_success(
        f"Profile refresh completed in {sec}s. "
        f"Accounts: {processed_accounts}/{total_accounts}, new profiles: {created_profiles}."
    )
    return True


def ensure_sso_session(cfg: Dict[str, str]) -> bool:
    ensure_aws_config_file()
    configure_first_connect(cfg)
    if is_sso_token_valid(cfg):
        return True

    msg_warn(f"SSO session '{cfg['awsDefaultSession']}' is expired or missing. Starting login...")
    proc = subprocess.run(
        ["aws", "sso", "login", "--sso-session", cfg["awsDefaultSession"]],
        text=True,
        env=aws_env_without_profile(),
    )
    if proc.returncode != 0:
        msg_error("Could not authenticate to AWS SSO.")
        return False
    return create_aws_profiles(cfg)


def _render_curses_menu(stdscr, title: str, options: List[str]) -> Optional[str]:
    """Render an interactive menu using curses with arrow key navigation."""
    curses.curs_set(0)  # Hide cursor
    stdscr.keypad(True)
    stdscr.nodelay(False)
    stdscr.clear()
    
    selected = 0
    
    while True:
        stdscr.clear()
        max_y, max_x = stdscr.getmaxyx()

        row = _ui_draw_frame(stdscr, title)
        
        # Display options
        for i, option in enumerate(options):
            if row >= max_y - 2:
                break
            if i == selected:
                sel_attr = _ui_color(_CP_SELECTED, curses.A_REVERSE) | curses.A_BOLD
                _ui_safe_add(stdscr, row, 0, f"> {option}", max_x, sel_attr)
            else:
                _ui_safe_add(stdscr, row, 0, f"  {option}", max_x)
            row += 1

        status_line = _UI_STATUS_LINE
        if status_line:
            level = status_line.split(" ", 1)[0] if " " in status_line else "INFO"
            _ui_safe_add(stdscr, max_y - 2, 0, status_line, max_x, _ui_status_attr(level))

        hint = "Use arrows to navigate, ENTER to select, ESC to cancel"
        hint_attr = _ui_color(_CP_HINT, curses.A_DIM) | curses.A_DIM
        _ui_safe_add(stdscr, max_y - 1, 0, hint, max_x, hint_attr)
        stdscr.refresh()
        
        # Get input
        key = stdscr.getch()
        
        if key == curses.KEY_UP:
            selected = (selected - 1) % len(options)
        elif key == curses.KEY_DOWN:
            selected = (selected + 1) % len(options)
        elif key == ord("\n") or key == ord("\r"):
            return options[selected]
        elif key == 27:  # ESC
            return None
        elif key == 3:  # Ctrl+C
            return None


def _close_curses_session() -> None:
    global _CURSES_STDSCR, _CURSES_TTY_IN, _CURSES_TTY_OUT, _SAVED_STDIN_FD, _SAVED_STDOUT_FD
    if _CURSES_STDSCR is not None:
        try:
            curses.nocbreak()
            _CURSES_STDSCR.keypad(False)
            curses.echo()
            curses.endwin()
        except Exception:
            pass
        _CURSES_STDSCR = None

    if _SAVED_STDIN_FD is not None:
        try:
            os.dup2(_SAVED_STDIN_FD, 0)
            os.close(_SAVED_STDIN_FD)
        except Exception:
            pass
        _SAVED_STDIN_FD = None

    if _SAVED_STDOUT_FD is not None:
        try:
            os.dup2(_SAVED_STDOUT_FD, 1)
            os.close(_SAVED_STDOUT_FD)
        except Exception:
            pass
        _SAVED_STDOUT_FD = None

    if _CURSES_TTY_IN is not None:
        try:
            _CURSES_TTY_IN.close()
        except Exception:
            pass
        _CURSES_TTY_IN = None

    if _CURSES_TTY_OUT is not None:
        try:
            _CURSES_TTY_OUT.close()
        except Exception:
            pass
        _CURSES_TTY_OUT = None


def _init_curses_session() -> bool:
    global _CURSES_STDSCR, _CURSES_TTY_IN, _CURSES_TTY_OUT, _SAVED_STDIN_FD, _SAVED_STDOUT_FD
    if _CURSES_STDSCR is not None:
        return True

    try:
        _CURSES_TTY_IN = open("/dev/tty", "r")
        _CURSES_TTY_OUT = open("/dev/tty", "w")
        _SAVED_STDIN_FD = os.dup(0)
        _SAVED_STDOUT_FD = os.dup(1)

        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass

        os.dup2(_CURSES_TTY_IN.fileno(), 0)
        os.dup2(_CURSES_TTY_OUT.fileno(), 1)

        _CURSES_STDSCR = curses.initscr()
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
            except Exception:
                pass
            # AWS-inspired palette: deep blue + amber accents.
            curses.init_pair(_CP_HEADER, curses.COLOR_WHITE, curses.COLOR_BLUE)
            curses.init_pair(_CP_COMPANY, curses.COLOR_YELLOW, -1)
            curses.init_pair(_CP_SELECTED, curses.COLOR_BLACK, curses.COLOR_YELLOW)
            curses.init_pair(_CP_INFO, curses.COLOR_CYAN, -1)
            curses.init_pair(_CP_WARN, curses.COLOR_YELLOW, -1)
            curses.init_pair(_CP_ERROR, curses.COLOR_RED, -1)
            curses.init_pair(_CP_HINT, curses.COLOR_BLUE, -1)
        curses.noecho()
        curses.cbreak()
        _CURSES_STDSCR.keypad(True)
        return True
    except Exception:
        _close_curses_session()
        return False


def choose_menu(title: str, options: List[str]) -> Optional[str]:
    if not options:
        return None

    # Keep a single curses session during the selection flow to avoid shell/UI flicker.
    if _init_curses_session() and _CURSES_STDSCR is not None:
        try:
            return _render_curses_menu(_CURSES_STDSCR, title, options)
        except Exception:
            _close_curses_session()
    
    # Fallback: numeric menu mode
    tty_out = None
    tty_in = None
    try:
        tty_out = open("/dev/tty", "w", buffering=1)
        tty_in = open("/dev/tty", "r")
    except Exception:
        pass

    display = tty_out or sys.stdout
    input_src = tty_in or sys.stdin

    try:
        print("\n" + title, file=display)
        display.flush()
        for i, item in enumerate(options, start=1):
            print(f" {i}. {item}", file=display)
            display.flush()
        
        while True:
            try:
                if tty_in:
                    display.write("Choose (number, empty to cancel): ")
                    display.flush()
                    raw = input_src.readline().strip()
                else:
                    raw = input("Choose (number, empty to cancel): ").strip()
                
                if not raw:
                    return None
                if raw.isdigit():
                    idx = int(raw)
                    if 1 <= idx <= len(options):
                        return options[idx - 1]
            except KeyboardInterrupt:
                print(file=display)
                display.flush()
                msg_warn("Cancelled by user.")
                return None
    finally:
        if tty_out:
            tty_out.close()
        if tty_in:
            tty_in.close()


def select_profile(cfg: Dict[str, str]) -> Optional[Tuple[str, str, str]]:
    _init_curses_session()
    login_attempts = 0
    while True:
        started = dt.datetime.now()
        msg_info("Loading accessible AWS accounts...")
        try:
            accounts = list_accessible_accounts(cfg)
        except Exception as exc:
            msg_error(str(exc))
            return None

        sec = int((dt.datetime.now() - started).total_seconds())
        if accounts:
            msg_success(f"Loaded {len(accounts)} accounts in {sec}s.")
        else:
            if login_attempts == 0:
                login_attempts += 1
                msg_warn("No accessible accounts found. Attempting SSO login...")
                if subprocess.run(
                    ["aws", "sso", "login", "--sso-session", cfg["awsDefaultSession"]],
                    text=True,
                    env=aws_env_without_profile(),
                ).returncode == 0:
                    msg_success("SSO login successful. Retrying account list...")
                    continue
            msg_error("No accessible accounts found.")
            return None

        mapping: Dict[str, Tuple[str, str]] = {}
        account_labels: List[str] = []
        preferred_account_id = cfg.get("lastAccountId", "").strip()
        for aid, aname in accounts:
            label = f"{aname} ({aid})"
            mapping[label] = (aid, aname)
            account_labels.append(label)

        preferred_label = ""
        if preferred_account_id:
            for label, (aid, _) in mapping.items():
                if aid == preferred_account_id:
                    preferred_label = label
                    break

        # More objective menu: only accounts and clear actions
        account_labels = _move_preferred_first(account_labels, preferred_label)
        choices = account_labels + ["Refresh/Reconfigure Profiles", "Clear Session", "Exit"]

        choice = choose_menu("Select AWS Account:", choices)
        if choice is None or choice == "Exit":
            return None
        if choice == "Clear Session":
            if ui_menu_active():
                _ui_flash_center("Session cleared.", 1.0, "OK")
            return ("__CLEAR__", "", "")
        if choice == "Refresh/Reconfigure Profiles":
            create_aws_profiles(cfg)
            continue

        if choice not in mapping:
            msg_warn("Invalid account selection. Please try again.")
            continue

        account_id, account_name = mapping[choice]
        roles = list_account_roles(cfg, account_id)
        if not roles:
            msg_warn(f"No roles found for account '{account_name}'.")
            continue

        if len(roles) == 1:
            selected_role = roles[0]
            msg_info(f"Single role found for '{account_name}': {selected_role}")
        else:
            preferred_role = cfg.get("lastRoleName", "").strip() if account_id == cfg.get("lastAccountId", "").strip() else ""
            ordered_roles = _move_preferred_first(roles, preferred_role)
            role_choice = choose_menu(f"Select role for '{account_name}':", ordered_roles + ["Exit"])
            if role_choice is None or role_choice == "Exit":
                continue
            selected_role = role_choice

        profile = build_profile_name(account_name, selected_role)
        create_profile_if_missing(cfg, profile, account_id, selected_role)
        cfg["lastAccountId"] = account_id
        cfg["lastAccountName"] = account_name
        cfg["lastRoleName"] = selected_role
        cfg["lastProfile"] = profile
        save_last_selection(
            {
                "lastAccountId": account_id,
                "lastAccountName": account_name,
                "lastRoleName": selected_role,
                "lastProfile": profile,
            }
        )
        return profile, account_name, selected_role


def _is_region_name_format_valid(region: str) -> bool:
    return bool(re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d", region.strip()))


def _load_regions_for_profile(profile: str, fallback_region: str) -> List[str]:
    cached = _REGION_CACHE_BY_PROFILE.get(profile)
    if cached is not None:
        return cached

    proc = subprocess.run(
        [
            "aws",
            "ec2",
            "describe-regions",
            "--all-regions",
            "--profile",
            profile,
            "--region",
            fallback_region,
            "--query",
            "Regions[].RegionName",
            "--output",
            "json",
        ],
        text=True,
        capture_output=True,
        env=aws_env_without_profile(),
    )
    if proc.returncode != 0:
        return []

    try:
        raw = json.loads(proc.stdout)
    except Exception:
        return []

    regions = sorted({str(r).strip() for r in raw if str(r).strip()})
    _REGION_CACHE_BY_PROFILE[profile] = regions
    return regions


def _validate_region_exists(region: str, profile: str, default_region: str) -> Tuple[bool, str]:
    candidate = region.strip()
    if not candidate:
        return False, "Region is required."
    if not _is_region_name_format_valid(candidate):
        return False, "Invalid region format. Example: us-east-1"

    known = _load_regions_for_profile(profile, default_region)
    if known:
        if candidate not in known:
            return False, f"Region '{candidate}' does not exist."
        return True, ""

    # Fallback: try dynamic regions file
    dynamic_regions = load_aws_regions()
    if dynamic_regions:
        if candidate not in dynamic_regions:
            return False, f"Region '{candidate}' is invalid or unsupported."
        return True, ""
    return False, "Could not validate region list right now. Run Refresh/Reconfigure Profiles and try again."


def _prompt_region_custom(default_region: str, profile: str) -> Optional[str]:
    if not ui_menu_active() or _CURSES_STDSCR is None:
        while True:
            typed = prompt_required("awsRegion (session override)", default_region).strip()
            ok, err = _validate_region_exists(typed, profile, default_region)
            if ok:
                return typed
            msg_warn(err)

    stdscr = _CURSES_STDSCR
    while True:
        max_y, max_x = stdscr.getmaxyx()
        stdscr.clear()
        row = _ui_draw_frame(stdscr, "Select AWS region for this session:")

        _ui_safe_add(stdscr, row, 0, f"Default region: {default_region}", max_x)
        _ui_safe_add(stdscr, row + 2, 0, "Custom region (press ENTER to validate):", max_x)
        _ui_safe_add(stdscr, row + 3, 0, "Region: ", max_x, curses.A_BOLD)
        if _UI_STATUS_LINE:
            level = _UI_STATUS_LINE.split(" ", 1)[0] if " " in _UI_STATUS_LINE else "INFO"
            _ui_safe_add(stdscr, max_y - 2, 0, _UI_STATUS_LINE, max_x, _ui_status_attr(level))
        hint_attr = _ui_color(_CP_HINT, curses.A_DIM) | curses.A_DIM
        _ui_safe_add(stdscr, max_y - 1, 0, "Type region, ENTER to confirm, blank uses default", max_x, hint_attr)
        stdscr.refresh()

        try:
            curses.echo()
            col = len("Region: ")
            stdscr.move(row + 3, col)
            stdscr.clrtoeol()
            raw = stdscr.getstr(row + 3, col, max(1, max_x - col - 1))
            curses.noecho()
        except Exception:
            try:
                curses.noecho()
            except Exception:
                pass
            return None

        typed = raw.decode("utf-8", errors="ignore").strip() if raw is not None else ""
        candidate = typed or default_region
        ok, err = _validate_region_exists(candidate, profile, default_region)
        if ok:
            return candidate

        msg_warn(err)
        _ui_flash_center(err, 1.2, "ERROR")


def select_session_region(cfg: Dict[str, str], profile: str) -> Optional[Tuple[str, bool]]:
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
        custom = _prompt_region_custom(default_region, profile)
        if not custom:
            return None
        return custom, True
    return default_region, False


def emit_shell_for_profile(profile: str, account_name: str, role_name: str, region: str) -> str:
    pa = sanitize_name(account_name)
    pr = sanitize_name(role_name)
    lines = [
        f"export AWS_PROFILE={shell_quote(profile)}",
        f"export PROFILE={shell_quote(profile)}",
        f"export AWS_REGION={shell_quote(region)}",
        f"export AWS_DEFAULT_REGION={shell_quote(region)}",
        "if [ -n \"$ZSH_VERSION\" ]; then",
        f"  export RPROMPT='%{{$fg[blue]%}}(ACC:{pa}-R:{pr})%{{$reset_color%}}'",
        "else",
        "  export _ORIG_PS1=\"${_ORIG_PS1:-$PS1}\"",
        f"  export PS1='\\[\\033[0;34m\\](ACC:{pa}-R:{pr})\\[\\033[0m\\] $_ORIG_PS1'",
        "fi",
    ]
    return "\n".join(lines)


def emit_shell_region(region: str, force_reset: bool = False) -> str:
    q = shell_quote(region)
    lines: List[str] = []
    if force_reset:
        lines.extend(["unset AWS_REGION", "unset AWS_DEFAULT_REGION"])
    lines.extend([f"export AWS_REGION={q}", f"export AWS_DEFAULT_REGION={q}"])
    return "\n".join(lines)


def emit_shell_clear() -> str:
    return "\n".join(
        [
            "unset AWS_PROFILE",
            "unset PROFILE",
            "unset AWS_ACCESS_KEY_ID",
            "unset AWS_SECRET_ACCESS_KEY",
            "unset AWS_SESSION_TOKEN",
            "unset AWS_CREDENTIAL_EXPIRATION",
            "unset AWS_REGION",
            "unset AWS_DEFAULT_REGION",
            "unset RPROMPT",
            "if [ -n \"$_ORIG_PS1\" ]; then",
            "  export PS1=\"$_ORIG_PS1\"",
            "  unset _ORIG_PS1",
            "fi",
        ]
    )


def detect_shell_profile() -> Path:
    shell = os.getenv("SHELL", "")
    if "zsh" in shell:
        p = Path.home() / ".zshrc"
        return p
    if "bash" in shell:
        p = Path.home() / ".bashrc"
        return p
    return Path.home() / ".profile"


def install_tool() -> bool:
    profile = detect_shell_profile()
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.touch(exist_ok=True)
    text = profile.read_text(encoding="utf-8")

    block = "\n".join(
        [
            "",
            "function awsswitch() {",
            f"\tsource {APP_DIR}/awsaccountstools.sh awsswitch",
            "}",
            "function eksswitch() {",
            f"\tsource {APP_DIR}/awsaccountstools.sh eksswitch",
            "}",
            "",
        ]
    )

    lines = [ln for ln in text.splitlines()]
    out: List[str] = []
    skipping = False
    for line in lines:
        if line.startswith("function awsswitch() {") or line.startswith("function eksswitch() {"):
            skipping = True
            continue
        if skipping and line.strip() == "}":
            skipping = False
            continue
        if not skipping:
            out.append(line)

    new_text = "\n".join(out).rstrip() + block
    profile.write_text(new_text + "\n", encoding="utf-8")
    msg_success(f"Installed! Reload your shell: source {profile}")
    return True


def remove_tool() -> bool:
    shell_files = [
        Path.home() / ".zprofile",
        Path.home() / ".zshrc",
        Path.home() / ".bash_profile",
        Path.home() / ".bashrc",
        Path.home() / ".profile",
    ]
    for fp in shell_files:
        if not fp.exists():
            continue
        lines = fp.read_text(encoding="utf-8").splitlines()
        out: List[str] = []
        skipping = False
        for line in lines:
            if line.startswith("function awsswitch() {") or line.startswith("function eksswitch() {"):
                skipping = True
                continue
            if skipping and line.strip() == "}":
                skipping = False
                continue
            if not skipping:
                out.append(line)
        fp.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    msg_success("awsaccountstools has been uninstalled.")
    return True


def do_configure(cfg: Dict[str, str]) -> bool:
    if not ensure_sso_session(cfg):
        return False
    configure_first_connect(cfg)
    return create_aws_profiles(cfg)


def do_healthcheck(cfg: Dict[str, str]) -> bool:
    ok = True
    checks_total = 0
    checks_passed = 0
    aws_cli_ok = False

    def _record_check(passed: bool) -> None:
        nonlocal checks_total, checks_passed
        checks_total += 1
        if passed:
            checks_passed += 1

    msg_info("Running healthcheck...")

    if require_aws_cli():
        aws_cli_ok = True
        _record_check(True)
        msg_success("AWS CLI: available")
    else:
        _record_check(False)
        msg_error("AWS CLI: not available")
        ok = False

    env_local = _env_local_path()
    if env_local.exists():
        _record_check(True)
        msg_success(f"Config file: found ({env_local})")
    else:
        _record_check(False)
        msg_error(f"Config file: missing ({env_local})")
        ok = False

    try:
        check_required_config(cfg)
        _record_check(True)
        msg_success("Required config: valid")
    except Exception as exc:
        _record_check(False)
        msg_error(f"Required config: invalid ({exc})")
        ok = False

    ensure_aws_config_file()
    session_section = f"[sso-session {cfg.get('awsDefaultSession', '')}]"
    aws_config_text = AWS_CONFIG.read_text(encoding="utf-8") if AWS_CONFIG.exists() else ""
    if session_section and session_section in aws_config_text:
        _record_check(True)
        msg_success(f"AWS config session: present ({cfg.get('awsDefaultSession', '')})")
    else:
        _record_check(False)
        msg_warn("AWS config session: missing, run configure/refresh to create it")
        ok = False

    regions = load_aws_regions()
    if regions:
        _record_check(True)
        msg_success(f"Region cache: {len(regions)} regions loaded")
    else:
        _record_check(False)
        msg_warn("Region cache: empty or unavailable, run Refresh/Reconfigure Profiles")
        ok = False

    if is_sso_token_valid(cfg):
        _record_check(True)
        msg_success("SSO token: valid")
        if aws_cli_ok:
            try:
                accounts = list_accessible_accounts(cfg)
                _record_check(True)
                msg_success(f"Accessible accounts: {len(accounts)}")
            except Exception as exc:
                _record_check(False)
                msg_error(f"Accessible accounts: failed ({exc})")
                ok = False
        else:
            msg_warn("Accessible accounts: skipped because AWS CLI is unavailable")
    else:
        _record_check(False)
        msg_warn("SSO token: expired or missing (run refresh to login)")
        ok = False
        msg_warn("Accessible accounts: skipped until SSO token is valid")

    msg_info(f"Healthcheck summary: {checks_passed}/{checks_total} checks passed")

    if ok:
        msg_success("Healthcheck passed.")
    else:
        msg_warn("Healthcheck completed with warnings/errors.")

    return ok


def prepare_profile_selection(cfg: Dict[str, str]) -> Optional[Tuple[str, str, str, str, bool, List[str]]]:
    if not ensure_sso_session(cfg):
        return None

    selected = select_profile(cfg)
    if selected is None:
        return None

    if selected[0] == "__CLEAR__":
        return ("__CLEAR__", "", "", "", False, [])

    profile, account_name, role_name = selected
    region_selection = select_session_region(cfg, profile)
    if not region_selection:
        return None
    region, is_custom_region = region_selection

    msg_success(f"Profile selected: {profile}")

    proc = subprocess.run(
        ["aws", "configure", "export-credentials", "--profile", profile, "--format", "env"],
        text=True,
        capture_output=True,
        env=aws_env_without_profile(),
    )
    if proc.returncode != 0:
        msg_error(proc.stderr.strip() or "Failed to export credentials")
        return None

    export_lines: List[str] = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if not upper.startswith("EXPORT "):
            continue
        if "AWS_REGION=" in upper or "AWS_DEFAULT_REGION=" in upper:
            continue
        export_lines.append(stripped)
    msg_info("Programmatic credentials exported.")
    return profile, account_name, role_name, region, is_custom_region, export_lines


def do_awsswitch(cfg: Dict[str, str], emit_shell: bool) -> int:
    if emit_shell:
        _init_curses_session()

    prepared = prepare_profile_selection(cfg)
    if prepared is None:
        if emit_shell:
            _close_curses_session()
        return 1

    profile, account_name, role_name, region, is_custom_region, export_lines = prepared
    if profile == "__CLEAR__":
        if emit_shell:
            msg_success("Cleared. Session profile and credentials unset.")
            _close_curses_session()
            print(emit_shell_clear())
        else:
            msg_warn("Clear Session requires shell mode to change environment variables.")
        return 0

    cfg["lastRegion"] = region
    save_last_selection({"lastRegion": region})

    if emit_shell:
        _ui_flash_center("Environment session configured successfully", 1.0, "OK")
        _close_curses_session()
        shell_lines = [emit_shell_for_profile(profile, account_name, role_name, region)]
        shell_lines.extend(export_lines)
        shell_lines.append(emit_shell_region(region, force_reset=is_custom_region))
        print("\n".join(shell_lines))
    return 0


def configure_eks(profile: str, region: str, preferred_cluster: str = "") -> Tuple[Optional[str], str, Optional[str]]:
    try:
        data = run_aws_json(
            [
                "eks",
                "list-clusters",
                "--profile",
                profile,
                "--region",
                region,
                "--query",
                "clusters[]",
                "--output",
                "json",
            ]
        )
    except Exception as exc:
        msg_error(str(exc))
        return None, "error", None

    clusters = [str(c).strip() for c in data if str(c).strip()]
    clusters.sort(key=str.lower)

    if preferred_cluster:
        clusters = _move_preferred_first(clusters, preferred_cluster)

    if not clusters:
        msg_warn(f"No EKS clusters found in region {region}.")
        return None, "no-clusters", None

    if len(clusters) == 1:
        cluster = clusters[0]
        msg_info(f"Single EKS cluster found: {cluster}")
    else:
        # Keep cluster options first so remembered cluster is immediately selected.
        choice = choose_menu("Select the EKS Cluster:", [*clusters, "Exit"])
        if choice is None or choice == "Exit":
            return None, "cancel", None
        cluster = choice

    kubeconfig = str(Path.home() / ".kube" / f"config-{profile}-{cluster}")
    msg_info(f"Connecting to EKS cluster: {cluster} ({region})")
    proc = subprocess.run(
        [
            "aws",
            "eks",
            "update-kubeconfig",
            "--name",
            cluster,
            "--profile",
            profile,
            "--region",
            region,
            "--kubeconfig",
            kubeconfig,
        ],
        text=True,
        capture_output=True,
        env=aws_env_without_profile(),
    )
    if proc.returncode != 0:
        msg_error(proc.stderr.strip() or proc.stdout.strip() or "Failed to update kubeconfig")
        return None, "error", None

    shell = [
        f"export KUBECONFIG={shell_quote(kubeconfig)}",
        f"if [ -n \"$ZSH_VERSION\" ]; then export RPROMPT='%{{$fg[blue]%}}(EKS: {cluster})%{{$reset_color%}}'; fi",
    ]
    return "\n".join(shell), "ok", cluster


def do_eksswitch(cfg: Dict[str, str], emit_shell: bool) -> int:
    if emit_shell:
        _init_curses_session()

    prepared = prepare_profile_selection(cfg)
    if prepared is None:
        if emit_shell:
            _close_curses_session()
        return 1
    profile, account_name, role_name, region, is_custom_region, export_lines = prepared

    if profile == "__CLEAR__":
        if emit_shell:
            msg_success("Cleared. Session profile and credentials unset.")
            _close_curses_session()
            print(emit_shell_clear())
        else:
            msg_warn("Clear Session requires shell mode to change environment variables.")
        return 0

    current_region = region
    current_is_custom = is_custom_region
    eks_shell: Optional[str] = None
    selected_cluster: Optional[str] = None
    preferred_cluster = cfg.get("lastCluster", "")

    while True:
        eks_shell, reason, selected_cluster = configure_eks(profile, current_region, preferred_cluster)
        if eks_shell is not None:
            break

        if reason == "no-clusters":
            next_action = choose_menu(
                "No clusters found for this region. What do you want to do?",
                ["Choose another region", "Cancel"],
            )
            if next_action == "Choose another region":
                region_selection = select_session_region(cfg, profile)
                if not region_selection:
                    if emit_shell:
                        _close_curses_session()
                    return 1
                current_region, current_is_custom = region_selection
                preferred_cluster = cfg.get("lastCluster", "")
                continue

        if emit_shell:
            _close_curses_session()
        return 1

    if emit_shell:
        _ui_flash_center("Environment session configured successfully", 1.0, "OK")
        _close_curses_session()
        shell_lines = [emit_shell_for_profile(profile, account_name, role_name, current_region)]
        shell_lines.extend(export_lines)
        shell_lines.append(emit_shell_region(current_region, force_reset=current_is_custom))
        shell_lines.append(eks_shell)
        print("\n".join(shell_lines))

    cfg["lastRegion"] = current_region
    save_values: Dict[str, str] = {"lastRegion": current_region}
    if selected_cluster:
        cfg["lastCluster"] = selected_cluster
        save_values.update(
            {
                "lastCluster": selected_cluster,
            }
        )

    save_last_selection(save_values)

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AWS SSO account tools (Python refactor)")
    p.add_argument("command", choices=["install", "remove", "uninstall", "configure", "refresh", "awsswitch", "eksswitch", "healthcheck", "help"])  # noqa: E501
    p.add_argument("--emit-shell", action="store_true", help="Emit shell export commands to stdout")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "help":
        parser.print_help()
        return 0

    if args.command in {"remove", "uninstall"}:
        return 0 if remove_tool() else 1

    cfg = load_env_config()
    if args.command in {"install", "configure", "refresh", "awsswitch", "eksswitch"}:
        try:
            cfg = ensure_env_local(cfg)
            if args.command == "configure":
                cfg = prompt_config_values(cfg, require_interactive=True)
            set_ui_company_name(cfg.get("awsCompanyName", "My Company"))
            set_ui_company_logo(cfg.get("awsCompanyLogo"))
            check_required_config(cfg)
        except Exception as exc:
            msg_error(str(exc))
            return 1

    if args.command == "healthcheck":
        set_ui_company_name(cfg.get("awsCompanyName", "My Company"))
        set_ui_company_logo(cfg.get("awsCompanyLogo"))
        return 0 if do_healthcheck(cfg) else 1

    if args.command in {"install", "configure", "refresh", "awsswitch", "eksswitch"}:
        if not require_aws_cli():
            return 1

    if args.command == "install":
        return 0 if install_tool() else 1

    if args.command in {"configure", "refresh"}:
        return 0 if do_configure(cfg) else 1

    if args.command == "awsswitch":
        return do_awsswitch(cfg, args.emit_shell)

    if args.command == "eksswitch":
        return do_eksswitch(cfg, args.emit_shell)

    parser.print_help()
    return 1


if __name__ == "__main__":
    atexit.register(_close_curses_session)
    raise SystemExit(main())
