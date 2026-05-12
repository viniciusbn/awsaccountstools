"""Shell integration: generate export/unset commands, install/remove shell functions.

This module produces shell code that is eval'd by the bash wrapper
(awsaccountstools.sh). It also manages the awsswitch/eksswitch function
definitions in the user's shell profile (~/.zshrc, ~/.bashrc, etc.).
"""

import os
import subprocess
from pathlib import Path
from typing import List

from .config import APP_DIR
from .ui import msg_success
from .utils import sanitize_name, shell_quote


def emit_shell_for_profile(
    profile: str, account_name: str, role_name: str, region: str,
) -> str:
    """Generate shell export commands to activate an AWS profile.

    Sets AWS_PROFILE, AWS_REGION, AWS_DEFAULT_REGION, and configures
    the shell prompt (RPROMPT for zsh, PS1 for bash) to display the
    active account and role.
    """
    pa = sanitize_name(account_name)
    pr = sanitize_name(role_name)
    return "\n".join([
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
    ])


def emit_shell_region(region: str, force_reset: bool = False) -> str:
    """Generate shell commands to set the AWS region variables.

    When force_reset is True, unsets existing region variables first
    to ensure a clean override (used with custom region selections).
    """
    q = shell_quote(region)
    lines: List[str] = []
    if force_reset:
        lines.extend(["unset AWS_REGION", "unset AWS_DEFAULT_REGION"])
    lines.extend([f"export AWS_REGION={q}", f"export AWS_DEFAULT_REGION={q}"])
    return "\n".join(lines)


def emit_shell_clear() -> str:
    """Generate shell commands to unset all AWS-related environment variables.

    Clears: AWS_PROFILE, PROFILE, credentials, region, and restores the
    original shell prompt.
    """
    return "\n".join([
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
    ])


def detect_shell_profile() -> Path:
    """Detect the most appropriate shell profile file.

        Detection order favors the active shell context over stale login defaults:
            1) Explicit override via AAT_ACTIVE_SHELL
            2) Parent process command name
            3) Exported shell version vars (ZSH_VERSION / BASH_VERSION)
            4) SHELL env var
            5) Reasonable file-based fallback
    """
    active_shell = os.getenv("AAT_ACTIVE_SHELL", "").strip().lower()
    if "zsh" in active_shell:
        return Path.home() / ".zshrc"
    if "bash" in active_shell:
        return Path.home() / ".bashrc"

    try:
        parent = subprocess.run(
            ["ps", "-p", str(os.getppid()), "-o", "comm="],
            text=True,
            capture_output=True,
        )
        comm = (parent.stdout or "").strip().lower()
        if "zsh" in comm:
            return Path.home() / ".zshrc"
        if "bash" in comm:
            return Path.home() / ".bashrc"
    except Exception:
        pass

    if os.getenv("ZSH_VERSION"):
        return Path.home() / ".zshrc"
    if os.getenv("BASH_VERSION"):
        return Path.home() / ".bashrc"

    shell = os.getenv("SHELL", "")
    if "zsh" in shell:
        return Path.home() / ".zshrc"
    if "bash" in shell:
        return Path.home() / ".bashrc"

    # Ambiguous environment fallback: prefer zshrc when present.
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        return zshrc
    return Path.home() / ".profile"


def _strip_shell_functions(text: str) -> str:
    """Remove existing awsswitch/eksswitch function definitions from shell profile text."""
    lines = text.splitlines()
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
    return "\n".join(out).rstrip()


def install_tool() -> bool:
    """Install awsswitch/eksswitch functions into the user's shell profile.

    Idempotent: removes any existing definitions before writing new ones.
    The functions source awsaccountstools.sh so that environment exports
    take effect in the current shell session.
    """
    profile = detect_shell_profile()
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.touch(exist_ok=True)
    text = profile.read_text(encoding="utf-8")
    cleaned = _strip_shell_functions(text)

    block = "\n".join([
        "",
        "function awsswitch() {",
        f"\tsource {APP_DIR}/awsaccountstools.sh awsswitch \"$@\"",
        "}",
        "function eksswitch() {",
        f"\tsource {APP_DIR}/awsaccountstools.sh eksswitch \"$@\"",
        "}",
        "",
    ])

    profile.write_text(cleaned + block + "\n", encoding="utf-8")
    msg_success(f"Installed! Reload your shell: source {profile}")
    return True


def remove_tool() -> bool:
    """Remove awsswitch/eksswitch functions from all known shell profile files."""
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
        text = fp.read_text(encoding="utf-8")
        fp.write_text(_strip_shell_functions(text) + "\n", encoding="utf-8")
    msg_success("awsaccountstools has been uninstalled.")
    return True
