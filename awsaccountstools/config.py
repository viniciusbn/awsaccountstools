"""Configuration management for multi-company AWS SSO environments.

Handles reading, migrating, and persisting configuration in JSON format
(with legacy KEY=VALUE fallback). Provides an interactive menu-driven
configure flow: add/edit companies via a full-screen editor, remove
companies with AWS config cleanup, and save/reset.
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .ui import choose_menu, edit_company, msg_error, msg_info, msg_success, msg_warn, prompt_required

APP_DIR = Path(__file__).resolve().parent.parent
AWS_CONFIG = Path.home() / ".aws" / "config"
DEFAULT_SSO_SCOPES = "sso:account:access"
AWS_REGIONS_FILE = APP_DIR / ".aws_regions"
_DEPRECATED_ENV_KEYS = {"lastClusterRegion", "lastClusterProfile"}


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


def _normalize_company(item: Dict[str, Any]) -> Dict[str, str]:
    return {
        "awsCompanyName": str(item.get("name", item.get("awsCompanyName", ""))).strip(),
        "awsStartURL": str(item.get("startUrl", item.get("awsStartURL", ""))).strip(),
        "awsDefaultSession": str(item.get("session", item.get("awsDefaultSession", ""))).strip(),
        "awsDefaultRegion": str(item.get("defaultRegion", item.get("awsDefaultRegion", ""))).strip(),
    }


def _legacy_to_doc(vals: Dict[str, str]) -> Dict[str, Any]:
    companies: List[Dict[str, str]] = []
    raw_companies = vals.get("awsCompaniesData", "").strip()
    if raw_companies:
        try:
            import base64
            decoded = base64.b64decode(raw_companies.encode("ascii")).decode("utf-8")
            parsed = json.loads(decoded)
            for item in parsed:
                c = _normalize_company(item)
                if all(c.values()):
                    companies.append(c)
        except Exception:
            pass

    if not companies and vals.get("awsStartURL") and vals.get("awsDefaultSession") and vals.get("awsDefaultRegion"):
        companies.append(
            {
                "awsCompanyName": vals.get("awsCompanyName", "My Company").strip() or "My Company",
                "awsStartURL": vals.get("awsStartURL", "").strip(),
                "awsDefaultSession": vals.get("awsDefaultSession", "").strip(),
                "awsDefaultRegion": vals.get("awsDefaultRegion", "").strip(),
            }
        )

    global_cache: Dict[str, str] = {}
    for key in ["lastAccountId", "lastAccountName", "lastRoleName", "lastProfile", "lastRegion", "lastCluster"]:
        if vals.get(key):
            global_cache[key] = vals[key]

    return {
        "version": 2,
        "companies": [
            {
                "name": c["awsCompanyName"],
                "startUrl": c["awsStartURL"],
                "session": c["awsDefaultSession"],
                "defaultRegion": c["awsDefaultRegion"],
            }
            for c in companies
        ],
        "activeCompany": companies[0]["awsCompanyName"] if companies else "",
        "selectionCache": {"global": global_cache, "byCompany": {}},
    }


def _read_config_doc(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            doc = json.loads(text)
            if isinstance(doc, dict):
                return doc
        except Exception:
            pass
    vals = parse_env_file(path)
    if not vals:
        return {}
    return _legacy_to_doc(vals)


def _merge_docs(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    if not override:
        return out
    for k in ["version", "activeCompany", "companies"]:
        if k in override and override[k]:
            out[k] = override[k]

    base_cache = base.get("selectionCache", {}) if isinstance(base.get("selectionCache", {}), dict) else {}
    over_cache = override.get("selectionCache", {}) if isinstance(override.get("selectionCache", {}), dict) else {}

    merged_global = dict(base_cache.get("global", {}))
    merged_global.update(over_cache.get("global", {}))

    merged_by_company = dict(base_cache.get("byCompany", {}))
    for company_name, cache_vals in over_cache.get("byCompany", {}).items():
        existing = dict(merged_by_company.get(company_name, {}))
        existing.update(cache_vals or {})
        merged_by_company[company_name] = existing

    out["selectionCache"] = {"global": merged_global, "byCompany": merged_by_company}
    return out


def _doc_to_runtime_cfg(doc: Dict[str, Any]) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    raw_companies = doc.get("companies", [])
    companies = [_normalize_company(c) for c in raw_companies if isinstance(c, dict)]
    companies = [c for c in companies if all(c.values())]

    active_name = str(doc.get("activeCompany", "")).strip()
    active_company = None
    if companies:
        active_company = next((c for c in companies if c["awsCompanyName"] == active_name), companies[0])
        cfg.update(active_company)

    selection_cache = doc.get("selectionCache", {}) if isinstance(doc.get("selectionCache", {}), dict) else {}
    global_cache = selection_cache.get("global", {}) if isinstance(selection_cache.get("global", {}), dict) else {}
    by_company = selection_cache.get("byCompany", {}) if isinstance(selection_cache.get("byCompany", {}), dict) else {}

    for k, v in global_cache.items():
        cfg[str(k)] = str(v)

    cfg["__companies"] = companies
    cfg["__activeCompany"] = active_company["awsCompanyName"] if active_company else ""
    cfg["__selectionGlobal"] = global_cache
    cfg["__selectionByCompany"] = by_company
    return cfg


def load_env_config() -> Dict[str, Any]:
    template_doc = _read_config_doc(APP_DIR / ".env")
    local_doc = _read_config_doc(APP_DIR / ".env.local")
    merged = _merge_docs(template_doc, local_doc)
    return _doc_to_runtime_cfg(merged)


def _env_local_path() -> Path:
    return APP_DIR / ".env.local"


def env_local_exists() -> bool:
    """Return True when .env.local already exists."""
    return _env_local_path().exists()


def _companies_to_doc_entries(companies: List[Dict[str, str]]) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    for c in companies:
        n = _normalize_company(c)
        if all(n.values()):
            entries.append(
                {
                    "name": n["awsCompanyName"],
                    "startUrl": n["awsStartURL"],
                    "session": n["awsDefaultSession"],
                    "defaultRegion": n["awsDefaultRegion"],
                }
            )
    return entries


def _write_doc(path: Path, doc: Dict[str, Any]) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_env_local(start_url: str, session: str, region: str, company: str, companies: Optional[List[Dict[str, str]]] = None) -> None:
    env_local = _env_local_path()
    existing = _read_config_doc(env_local)
    existing_cache = existing.get("selectionCache", {}) if isinstance(existing.get("selectionCache", {}), dict) else {}

    if companies is None:
        companies = [{"awsCompanyName": company, "awsStartURL": start_url, "awsDefaultSession": session, "awsDefaultRegion": region}]

    company_names = {c["awsCompanyName"] for c in companies if c.get("awsCompanyName")}
    existing_by_company = dict(existing_cache.get("byCompany", {})) if isinstance(existing_cache.get("byCompany", {}), dict) else {}
    filtered_by_company = {
        k: v for k, v in existing_by_company.items()
        if k in company_names
    }

    doc = {
        "version": 2,
        "companies": _companies_to_doc_entries(companies),
        "activeCompany": (company or (companies[0].get("awsCompanyName") if companies else "")).strip(),
        "selectionCache": {
            "global": dict(existing_cache.get("global", {})) if isinstance(existing_cache.get("global", {}), dict) else {},
            "byCompany": filtered_by_company,
        },
    }
    _write_doc(env_local, doc)
    os.chmod(env_local, 0o600)


def _update_local_doc(mutator) -> None:
    env_local = _env_local_path()
    doc = _read_config_doc(env_local)
    if not doc:
        cfg = load_env_config()
        companies = load_companies(cfg)
        doc = {
            "version": 2,
            "companies": _companies_to_doc_entries(companies),
            "activeCompany": companies[0]["awsCompanyName"] if companies else "",
            "selectionCache": {"global": {}, "byCompany": {}},
        }

    if "selectionCache" not in doc or not isinstance(doc["selectionCache"], dict):
        doc["selectionCache"] = {"global": {}, "byCompany": {}}
    if "global" not in doc["selectionCache"] or not isinstance(doc["selectionCache"]["global"], dict):
        doc["selectionCache"]["global"] = {}
    if "byCompany" not in doc["selectionCache"] or not isinstance(doc["selectionCache"]["byCompany"], dict):
        doc["selectionCache"]["byCompany"] = {}

    mutator(doc)
    _write_doc(env_local, doc)
    os.chmod(env_local, 0o600)


def save_last_selection(values: Dict[str, str]) -> None:
    keys_to_set = {k: v for k, v in values.items() if v is not None and str(v).strip() != ""}
    if not keys_to_set:
        return

    def _mutate(doc: Dict[str, Any]) -> None:
        global_cache = doc["selectionCache"]["global"]
        for k, v in keys_to_set.items():
            if k in _DEPRECATED_ENV_KEYS:
                continue
            global_cache[k] = str(v)

    _update_local_doc(_mutate)


def aws_env_without_profile() -> Dict[str, str]:
    env = os.environ.copy()
    env.pop("AWS_PROFILE", None)
    env.pop("AWS_DEFAULT_PROFILE", None)
    env.pop("PROFILE", None)
    return env


def load_companies(cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    if isinstance(cfg.get("__companies"), list):
        return [dict(c) for c in cfg.get("__companies", []) if isinstance(c, dict)]

    if cfg.get("awsStartURL") and cfg.get("awsDefaultSession") and cfg.get("awsDefaultRegion"):
        return [{
            "awsCompanyName": cfg.get("awsCompanyName", "My Company").strip() or "My Company",
            "awsStartURL": cfg.get("awsStartURL", "").strip(),
            "awsDefaultSession": cfg.get("awsDefaultSession", "").strip(),
            "awsDefaultRegion": cfg.get("awsDefaultRegion", "").strip(),
        }]

    return []


def check_companies_config(companies: List[Dict[str, str]]) -> None:
    for idx, company in enumerate(companies, start=1):
        missing = [k for k in ["awsCompanyName", "awsStartURL", "awsDefaultSession", "awsDefaultRegion"] if not company.get(k)]
        if missing:
            raise RuntimeError(f"Company #{idx} is missing required keys: {', '.join(missing)}")


def get_company_last_selection(cfg: Dict[str, Any], company_name: str) -> Dict[str, str]:
    by_company = cfg.get("__selectionByCompany", {})
    if not isinstance(by_company, dict):
        return {}
    data = by_company.get(company_name, {})
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if str(v).strip()}


def save_company_last_selection(company_name: str, values: Dict[str, str]) -> None:
    keys_to_set = {k: v for k, v in values.items() if v is not None and str(v).strip() != ""}
    if not keys_to_set:
        return

    def _mutate(doc: Dict[str, Any]) -> None:
        by_company = doc["selectionCache"]["byCompany"]
        company_cache = by_company.setdefault(company_name, {})
        for k, v in keys_to_set.items():
            if k in _DEPRECATED_ENV_KEYS:
                continue
            company_cache[k] = str(v)

    _update_local_doc(_mutate)


def ensure_env_local(cfg: Dict[str, Any]) -> Dict[str, Any]:
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


def prompt_config_values(cfg: Dict[str, Any], require_interactive: bool = False) -> Dict[str, Any]:
    if not sys.stdin.isatty():
        if require_interactive:
            raise RuntimeError("configure requires an interactive shell to edit values")
        return cfg

    msg_info("Configure your companies using the interactive menu.")

    previous_companies = load_companies(cfg)
    previous_sessions = {
        c.get("awsDefaultSession", "").strip()
        for c in previous_companies
        if c.get("awsDefaultSession", "").strip()
    }

    configured_companies = _interactive_companies_menu(cfg)
    check_companies_config(configured_companies)
    configured_sessions = {
        c.get("awsDefaultSession", "").strip()
        for c in configured_companies
        if c.get("awsDefaultSession", "").strip()
    }
    removed_sessions = sorted(previous_sessions - configured_sessions)

    if configured_companies:
        default_company = configured_companies[0]
        _write_env_local(
            default_company["awsStartURL"],
            default_company["awsDefaultSession"],
            default_company["awsDefaultRegion"],
            default_company["awsCompanyName"],
            companies=configured_companies,
        )
    else:
        _write_env_local("", "", "", "", companies=[])
        msg_warn("Managed companies list is empty. Managed configuration has been reset.")

    if removed_sessions:
        removed_sso, removed_profiles = _remove_aws_config_sections_for_sessions(removed_sessions)
        msg_info(
            "AWS config cleanup completed: "
            f"{removed_sso} session block(s), {removed_profiles} profile block(s) removed."
        )

    msg_success("Environment configuration saved to .env.local (JSON).")
    return load_env_config()


def _parse_config_blocks() -> List[Tuple[Optional[str], List[str], Dict[str, str]]]:
    """Parse ~/.aws/config into a list of (header, raw_lines, key_values).

    Each entry represents one INI section (or the preamble with header=None).
    key_values is a lowercase dict of the key=value pairs inside the block.
    """
    if not AWS_CONFIG.exists():
        return []

    text = AWS_CONFIG.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    header_re = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    keyval_re = re.compile(r"^\s*([A-Za-z0-9_\-\.]+)\s*=\s*(.*?)\s*$")

    raw_blocks: List[Tuple[Optional[str], List[str]]] = []
    current_header: Optional[str] = None
    current_lines: List[str] = []

    for line in lines:
        m = header_re.match(line.rstrip("\n"))
        if m:
            raw_blocks.append((current_header, current_lines))
            current_header = m.group(1).strip()
            current_lines = [line]
            continue
        current_lines.append(line)
    raw_blocks.append((current_header, current_lines))

    result: List[Tuple[Optional[str], List[str], Dict[str, str]]] = []
    for header, block_lines in raw_blocks:
        keys: Dict[str, str] = {}
        for raw in block_lines[1:]:
            km = keyval_re.match(raw.strip())
            if km:
                keys[km.group(1).strip().lower()] = km.group(2).strip()
        result.append((header, block_lines, keys))

    return result


def _remove_aws_config_sections_for_sessions(removed_sessions: List[str]) -> Tuple[int, int]:
    """Remove [sso-session] and [profile] blocks tied to removed sessions."""
    if not removed_sessions:
        return (0, 0)

    ensure_aws_config_file()
    blocks = _parse_config_blocks()
    if not blocks:
        return (0, 0)

    removed_set = set(removed_sessions)
    kept: List[str] = []
    removed_sso = 0
    removed_profiles = 0

    for header, block_lines, keys in blocks:
        if header is None:
            kept.extend(block_lines)
            continue

        low = header.lower()
        if low.startswith("sso-session "):
            session_name = header[len("sso-session "):].strip()
            if session_name in removed_set:
                removed_sso += 1
                continue
            kept.extend(block_lines)
            continue

        if low.startswith("profile "):
            sess = keys.get("sso_session", "")
            if sess in removed_set:
                removed_profiles += 1
                continue

        kept.extend(block_lines)

    AWS_CONFIG.write_text("".join(kept), encoding="utf-8")
    return (removed_sso, removed_profiles)


def preview_aws_config_cleanup_for_sessions(sessions: List[str]) -> Dict[str, Dict[str, int]]:
    """Dry-run: count blocks that would be removed for each session.

    Returns:
      {
        "session-name": {"ssoSessions": 1, "profiles": N},
        ...
      }
    """
    _, details = _preview_aws_config_cleanup_for_sessions(sessions)
    out: Dict[str, Dict[str, int]] = {}
    for name, (sso_count, profile_count) in details.items():
        out[name] = {"ssoSessions": sso_count, "profiles": profile_count}
    return out


def _preview_aws_config_cleanup_for_sessions(sessions: List[str]) -> Tuple[List[str], Dict[str, Tuple[int, int]]]:
    """Internal dry-run: count blocks that would be removed for each session."""
    if not sessions:
        return ([], {})

    ensure_aws_config_file()
    blocks = _parse_config_blocks()
    target_set = set(sessions)
    details: Dict[str, Tuple[int, int]] = {name: (0, 0) for name in sessions}

    for header, block_lines, keys in blocks:
        if header is None:
            continue

        low = header.lower()
        if low.startswith("sso-session "):
            session_name = header[len("sso-session "):].strip()
            if session_name in target_set:
                sso_count, profile_count = details.get(session_name, (0, 0))
                details[session_name] = (sso_count + 1, profile_count)
            continue

        if low.startswith("profile "):
            sess = keys.get("sso_session", "")
            if sess in target_set:
                sso_count, profile_count = details.get(sess, (0, 0))
                details[sess] = (sso_count, profile_count + 1)

    return (sessions, details)


def _interactive_companies_menu(cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    companies = load_companies(cfg)

    while True:
        options = [
            f"Add/Configure companies ({len(companies)})",
            "Remove companies",
            "Save and continue",
        ]
        selected = choose_menu("Configure companies", options)

        if selected is None:
            msg_info("Configure cancelled. Keeping current values.")
            return companies

        if selected.startswith("Add/Configure"):
            companies = _interactive_add_or_configure(companies)
            continue

        if selected == "Remove companies":
            if not companies:
                msg_warn("No companies available to remove.")
                continue
            companies = _interactive_remove_companies(companies)
            continue

        if companies:
            msg_success(f"Configuration ready with {len(companies)} compan(y/ies).")
        else:
            msg_warn("Configuration ready with 0 managed companies.")
        return companies


def _interactive_add_or_configure(companies: List[Dict[str, str]]) -> List[Dict[str, str]]:
    while True:
        options = [
            *[
                f"Edit #{idx}: {c.get('awsCompanyName', 'Unnamed')} ({c.get('awsDefaultSession', '-')})"
                for idx, c in enumerate(companies, start=1)
            ],
            "Add new company",
            "Back",
        ]
        selected = choose_menu("Add/Configure companies", options)
        if selected is None or selected == "Back":
            return companies

        if selected == "Add new company":
            new_company = _prompt_company_values({
                "awsCompanyName": "",
                "awsStartURL": "",
                "awsDefaultSession": "",
                "awsDefaultRegion": "us-east-1",
            }, len(companies) + 1, is_new=True)
            if new_company is None:
                msg_info("Add company cancelled.")
                continue
            companies.append(new_company)
            msg_success(f"Company '{new_company['awsCompanyName']}' added.")
            continue

        edit_idx = options.index(selected)
        if 0 <= edit_idx < len(companies):
            edited = _prompt_company_values(companies[edit_idx], edit_idx + 1)
            if edited is None:
                msg_info("Edit cancelled.")
                continue
            companies[edit_idx] = edited
            msg_success(f"Company '{edited['awsCompanyName']}' updated.")


def _prompt_company_values(existing: Dict[str, str], number: int, is_new: bool = False) -> Optional[Dict[str, str]]:
    msg_info(f"Configuring company #{number}")
    return edit_company(existing, number=number, is_new=is_new)


def _interactive_remove_companies(companies: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Remove companies through the same arrow-menu style used by add/configure."""
    while True:
        if not companies:
            msg_info("No companies left to remove.")
            return companies

        options = [
            *[
                f"Remove #{idx}: {c.get('awsCompanyName', 'Unnamed')} ({c.get('awsDefaultSession', '-')})"
                for idx, c in enumerate(companies, start=1)
            ],
            "Back",
        ]

        selected = choose_menu("Remove companies", options)
        if selected is None or selected == "Back":
            msg_info("Removal finished.")
            return companies

        remove_idx = options.index(selected)
        if remove_idx < 0 or remove_idx >= len(companies):
            msg_warn("Invalid selection.")
            continue

        company = companies[remove_idx]
        company_name = company.get("awsCompanyName", "Unnamed")
        confirm = choose_menu(
            f"Confirm removal of '{company_name}'?",
            ["Yes, remove", "No, back"],
        )
        if confirm != "Yes, remove":
            msg_info("Removal cancelled for selected company.")
            continue

        del companies[remove_idx]
        msg_success(f"Company '{company_name}' removed.")
        if not companies:
            msg_warn("All managed companies were removed.")
            return companies


def require_aws_cli() -> bool:
    if shutil.which("aws") is None:
        msg_error("AWS CLI is not installed and is required.")
        return False
    return True


def ensure_aws_config_file() -> None:
    AWS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    AWS_CONFIG.touch(exist_ok=True)
