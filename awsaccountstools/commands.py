"""Top-level command implementations: awsswitch, eksswitch, healthcheck, configure.

Orchestrates the full user flow: SSO session management, interactive
account/role/region selection, credential export, EKS cluster connection,
and system healthcheck diagnostics.
"""

import datetime as dt
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .aws import (
    configure_first_connect,
    create_aws_profiles,
    create_profile_if_missing,
    ensure_sso_session,
    is_sso_token_valid,
    list_accessible_accounts,
    list_account_roles,
    run_aws_json,
)
from .config import (
    AWS_CONFIG,
    _env_local_path,
    aws_env_without_profile,
    check_required_config,
    ensure_aws_config_file,
    require_aws_cli,
    save_last_selection,
)
from .regions import load_aws_regions, select_session_region
from .shell import emit_shell_clear, emit_shell_for_profile, emit_shell_region
from .ui import (
    choose_menu,
    close_ui,
    flash_center,
    init_ui,
    is_ui_active,
    msg_error,
    msg_info,
    msg_success,
    msg_warn,
)
from .utils import build_profile_name, move_preferred_first, shell_quote


# ---------------------------------------------------------------------------
# Profile selection
# ---------------------------------------------------------------------------

def select_profile(cfg: Dict[str, str]) -> Optional[Tuple[str, str, str]]:
    """Interactive account and role selection with SSO auto-login.

    Displays a menu of accessible AWS accounts (last-selected first),
    then a role menu if multiple roles exist. Handles Clear Session
    and Refresh/Reconfigure inline. Returns (profile, account_name,
    role_name) or None on cancel.
    """
    init_ui()
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
                if not is_sso_token_valid(cfg):
                    msg_warn("No accessible accounts found and token is invalid. Attempting SSO login...")
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

        account_labels = move_preferred_first(account_labels, preferred_label)
        choices = account_labels + ["Refresh/Reconfigure Profiles", "Clear Session", "Exit"]

        choice = choose_menu("Select AWS Account:", choices)
        if choice is None or choice == "Exit":
            return None
        if choice == "Clear Session":
            if is_ui_active():
                flash_center("Session cleared.", 1.0, "OK")
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
            preferred_role = (
                cfg.get("lastRoleName", "").strip()
                if account_id == cfg.get("lastAccountId", "").strip()
                else ""
            )
            ordered_roles = move_preferred_first(roles, preferred_role)
            role_choice = choose_menu(
                f"Select role for '{account_name}':", ordered_roles + ["Exit"],
            )
            if role_choice is None or role_choice == "Exit":
                continue
            selected_role = role_choice

        profile = build_profile_name(account_name, selected_role)
        create_profile_if_missing(cfg, profile, account_id, selected_role)

        cfg["lastAccountId"] = account_id
        cfg["lastAccountName"] = account_name
        cfg["lastRoleName"] = selected_role
        cfg["lastProfile"] = profile
        save_last_selection({
            "lastAccountId": account_id,
            "lastAccountName": account_name,
            "lastRoleName": selected_role,
            "lastProfile": profile,
        })
        return profile, account_name, selected_role


# ---------------------------------------------------------------------------
# Shared helpers for awsswitch / eksswitch
# ---------------------------------------------------------------------------

def _export_credentials(profile: str) -> Optional[List[str]]:
    """Export temporary credentials for a profile as shell export lines.

    Filters out region-related exports (handled separately) and returns
    only credential variables (AWS_ACCESS_KEY_ID, etc.).
    """
    proc = subprocess.run(
        ["aws", "configure", "export-credentials", "--profile", profile, "--format", "env"],
        text=True, capture_output=True, env=aws_env_without_profile(),
    )
    if proc.returncode != 0:
        msg_error(proc.stderr.strip() or "Failed to export credentials")
        return None

    lines: List[str] = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if not upper.startswith("EXPORT "):
            continue
        if "AWS_REGION=" in upper or "AWS_DEFAULT_REGION=" in upper:
            continue
        lines.append(stripped)
    return lines


def prepare_profile_selection(
    cfg: Dict[str, str],
) -> Optional[Tuple[str, str, str, str, bool, List[str]]]:
    """Full selection pipeline: SSO session → profile → region → credentials.

    Returns (profile, account_name, role_name, region, is_custom, export_lines)
    or None on failure/cancel.
    """
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
    export_lines = _export_credentials(profile)
    if export_lines is None:
        return None
    msg_info("Programmatic credentials exported.")
    return profile, account_name, role_name, region, is_custom_region, export_lines


def _handle_clear(emit_shell: bool) -> int:
    """Handle the 'Clear Session' action — unset all AWS env vars."""
    if emit_shell:
        msg_success("Cleared. Session profile and credentials unset.")
        close_ui()
        print(emit_shell_clear())
    else:
        msg_warn("Clear Session requires shell mode to change environment variables.")
    return 0


def _emit_base_shell(
    profile: str, account_name: str, role_name: str,
    region: str, is_custom_region: bool, export_lines: List[str],
) -> None:
    """Print the common shell export block (profile + credentials + region)."""
    shell_lines = [emit_shell_for_profile(profile, account_name, role_name, region)]
    shell_lines.extend(export_lines)
    shell_lines.append(emit_shell_region(region, force_reset=is_custom_region))
    print("\n".join(shell_lines))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def do_awsswitch(cfg: Dict[str, str], emit_shell: bool) -> int:
    """Switch AWS account/role and configure the shell environment.

    When emit_shell is True, prints export commands to stdout for eval
    by the bash wrapper. Otherwise, operates in display-only mode.
    """
    if emit_shell:
        init_ui()

    prepared = prepare_profile_selection(cfg)
    if prepared is None:
        if emit_shell:
            close_ui()
        return 1

    profile, account_name, role_name, region, is_custom_region, export_lines = prepared
    if profile == "__CLEAR__":
        return _handle_clear(emit_shell)

    cfg["lastRegion"] = region
    save_last_selection({"lastRegion": region})

    if emit_shell:
        flash_center("Environment session configured successfully", 1.0, "OK")
        close_ui()
        _emit_base_shell(profile, account_name, role_name, region, is_custom_region, export_lines)
    return 0


def configure_eks(
    profile: str, region: str, preferred_cluster: str = "",
) -> Tuple[Optional[str], str, Optional[str]]:
    """List EKS clusters, let the user pick one, and update kubeconfig.

    Returns (shell_commands, status, cluster_name) where status is one of:
    'ok', 'no-clusters', 'cancel', or 'error'.
    """
    try:
        data = run_aws_json([
            "eks", "list-clusters",
            "--profile", profile,
            "--region", region,
            "--query", "clusters[]",
            "--output", "json",
        ])
    except Exception as exc:
        msg_error(str(exc))
        return None, "error", None

    clusters = sorted(
        [str(c).strip() for c in data if str(c).strip()],
        key=str.lower,
    )
    if preferred_cluster:
        clusters = move_preferred_first(clusters, preferred_cluster)

    if not clusters:
        msg_warn(f"No EKS clusters found in region {region}.")
        return None, "no-clusters", None

    if len(clusters) == 1:
        cluster = clusters[0]
        msg_info(f"Single EKS cluster found: {cluster}")
    else:
        choice = choose_menu("Select the EKS Cluster:", [*clusters, "Exit"])
        if choice is None or choice == "Exit":
            return None, "cancel", None
        cluster = choice

    kubeconfig = str(Path.home() / ".kube" / f"config-{profile}-{cluster}")
    msg_info(f"Connecting to EKS cluster: {cluster} ({region})")
    proc = subprocess.run(
        [
            "aws", "eks", "update-kubeconfig",
            "--name", cluster,
            "--profile", profile,
            "--region", region,
            "--kubeconfig", kubeconfig,
        ],
        text=True, capture_output=True, env=aws_env_without_profile(),
    )
    if proc.returncode != 0:
        msg_error(proc.stderr.strip() or proc.stdout.strip() or "Failed to update kubeconfig")
        return None, "error", None

    shell = "\n".join([
        f"export KUBECONFIG={shell_quote(kubeconfig)}",
        f"if [ -n \"$ZSH_VERSION\" ]; then "
        f"export RPROMPT='%{{$fg[blue]%}}(EKS: {cluster})%{{$reset_color%}}'; fi",
    ])
    return shell, "ok", cluster


def do_eksswitch(cfg: Dict[str, str], emit_shell: bool) -> int:
    """Switch AWS account/role, then connect to an EKS cluster.

    Combines the awsswitch flow with EKS cluster selection. If no
    clusters exist in the selected region, offers to switch regions.
    """
    if emit_shell:
        init_ui()

    prepared = prepare_profile_selection(cfg)
    if prepared is None:
        if emit_shell:
            close_ui()
        return 1

    profile, account_name, role_name, region, is_custom_region, export_lines = prepared
    if profile == "__CLEAR__":
        return _handle_clear(emit_shell)

    current_region = region
    current_is_custom = is_custom_region
    preferred_cluster = cfg.get("lastCluster", "")

    while True:
        eks_shell, reason, selected_cluster = configure_eks(
            profile, current_region, preferred_cluster,
        )
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
                        close_ui()
                    return 1
                current_region, current_is_custom = region_selection
                preferred_cluster = cfg.get("lastCluster", "")
                continue

        if emit_shell:
            close_ui()
        return 1

    if emit_shell:
        flash_center("Environment session configured successfully", 1.0, "OK")
        close_ui()
        _emit_base_shell(
            profile, account_name, role_name,
            current_region, current_is_custom, export_lines,
        )
        print(eks_shell)

    cfg["lastRegion"] = current_region
    save_values: Dict[str, str] = {"lastRegion": current_region}
    if selected_cluster:
        cfg["lastCluster"] = selected_cluster
        save_values["lastCluster"] = selected_cluster
    save_last_selection(save_values)
    return 0


def do_configure(cfg: Dict[str, str]) -> bool:
    """Run initial setup: SSO login + profile refresh."""
    if not ensure_sso_session(cfg):
        return False
    configure_first_connect(cfg)
    return create_aws_profiles(cfg)


def do_healthcheck(cfg: Dict[str, str]) -> bool:
    """Run diagnostic checks and report system readiness.

    Validates: AWS CLI availability, config file presence, required
    config keys, SSO session section, region cache, token validity,
    and account enumeration. Returns True if all checks pass.
    """
    ok = True
    checks_total = 0
    checks_passed = 0
    aws_cli_ok = False

    def _record(passed: bool) -> None:
        nonlocal checks_total, checks_passed
        checks_total += 1
        if passed:
            checks_passed += 1

    msg_info("Running healthcheck...")

    if require_aws_cli():
        aws_cli_ok = True
        _record(True)
        msg_success("AWS CLI: available")
    else:
        _record(False)
        msg_error("AWS CLI: not available")
        ok = False

    env_local = _env_local_path()
    if env_local.exists():
        _record(True)
        msg_success(f"Config file: found ({env_local})")
    else:
        _record(False)
        msg_error(f"Config file: missing ({env_local})")
        ok = False

    try:
        check_required_config(cfg)
        _record(True)
        msg_success("Required config: valid")
    except Exception as exc:
        _record(False)
        msg_error(f"Required config: invalid ({exc})")
        ok = False

    ensure_aws_config_file()
    session_section = f"[sso-session {cfg.get('awsDefaultSession', '')}]"
    aws_config_text = AWS_CONFIG.read_text(encoding="utf-8") if AWS_CONFIG.exists() else ""
    if session_section and session_section in aws_config_text:
        _record(True)
        msg_success(f"AWS config session: present ({cfg.get('awsDefaultSession', '')})")
    else:
        _record(False)
        msg_warn("AWS config session: missing, run configure/refresh to create it")
        ok = False

    regions = load_aws_regions()
    if regions:
        _record(True)
        msg_success(f"Region cache: {len(regions)} regions loaded")
    else:
        _record(False)
        msg_warn("Region cache: empty or unavailable, run Refresh/Reconfigure Profiles")
        ok = False

    if is_sso_token_valid(cfg):
        _record(True)
        msg_success("SSO token: valid")
        if aws_cli_ok:
            try:
                accounts = list_accessible_accounts(cfg)
                _record(True)
                msg_success(f"Accessible accounts: {len(accounts)}")
            except Exception as exc:
                _record(False)
                msg_error(f"Accessible accounts: failed ({exc})")
                ok = False
        else:
            msg_warn("Accessible accounts: skipped because AWS CLI is unavailable")
    else:
        _record(False)
        msg_warn("SSO token: expired or missing (run refresh to login)")
        ok = False
        msg_warn("Accessible accounts: skipped until SSO token is valid")

    msg_info(f"Healthcheck summary: {checks_passed}/{checks_total} checks passed")
    if ok:
        msg_success("Healthcheck passed.")
    else:
        msg_warn("Healthcheck completed with warnings/errors.")
    return ok
