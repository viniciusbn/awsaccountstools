#!/usr/bin/env bash
# =============================================================================
# AWS Accounts Tools — Shell Wrapper
# =============================================================================
# This script MUST be sourced (not executed) for awsswitch/eksswitch so that
# environment variables (AWS_PROFILE, AWS_REGION, etc.) are set in the
# caller's shell session. Other commands (install, healthcheck, etc.) can
# be run normally since they don't modify the shell environment.
#
# Usage:
#   source awsaccountstools.sh awsswitch        # Interactive account switch
#   source awsaccountstools.sh awsswitch last   # Reuse last selection (no prompts)
#   source awsaccountstools.sh eksswitch        # Account switch + EKS cluster
#   source awsaccountstools.sh eksswitch last   # Reuse last EKS selection
#   ./awsaccountstools.sh healthcheck      # Diagnostic checks
#   ./awsaccountstools.sh help             # Show available commands
#
# Security: The eval in run_switch_and_eval() only processes lines matching
# strict awk patterns (export, unset, if/else/fi, typeset -gx) to prevent
# arbitrary code execution from Python output.
# =============================================================================

# Resolve the directory where this script lives (works for both bash and zsh)
if [ -n "$BASH_VERSION" ]; then
  APP_DIR=$(dirname "$(realpath "$BASH_SOURCE")")
else
  APP_DIR=$(dirname "$(realpath "$0")")
fi

# Abort in sourced context (return) or exit in executed context
abort_or_return() {
  return 1 2>/dev/null || exit 1
}

# Invoke the Python package, setting PYTHONPATH so the package is importable
run_python() {
  if command -v python3 >/dev/null 2>&1; then
    local active_shell=""

    # Prefer the caller shell (parent process), which is reliable even when
    # this script runs via bash shebang.
    local parent_comm
    parent_comm=$(ps -p "$PPID" -o comm= 2>/dev/null | tr '[:upper:]' '[:lower:]')
    case "$parent_comm" in
      *zsh*) active_shell="zsh" ;;
      *bash*) active_shell="bash" ;;
    esac

    # Fallback to runtime shell vars.
    if [ -z "$active_shell" ] && [ -n "$ZSH_VERSION" ]; then
      active_shell="zsh"
    fi
    if [ -z "$active_shell" ] && [ -n "$BASH_VERSION" ]; then
      active_shell="bash"
    fi

    # Last fallback to login shell from SHELL env var.
    if [ -z "$active_shell" ]; then
      case "$SHELL" in
      *zsh*) active_shell="zsh" ;;
      *bash*) active_shell="bash" ;;
      esac
    fi

    AAT_ACTIVE_SHELL="$active_shell" PYTHONPATH="$APP_DIR" python3 -m awsaccountstools "$@"
  else
    echo "python3 is required to run awsaccountstools." >&2
    return 1
  fi
}

# Run a switch command (awsswitch/eksswitch) and eval the filtered shell output.
# The Python script prints export/unset commands to stdout (--emit-shell flag).
# An awk filter whitelist ensures only safe shell statements are eval'd.
run_switch_and_eval() {
  local cmd="$1"
  shift
  local shell_out
  local filtered_out

  shell_out=$(run_python "$cmd" "$@" --emit-shell) || return 1
  if [ -n "$shell_out" ]; then
    filtered_out=$(printf '%s\n' "$shell_out" | awk '
      /^[[:space:]]*export[[:space:]]+[A-Za-z_][A-Za-z0-9_]*=.*/ { print; next }
      /^[[:space:]]*unset[[:space:]]+[A-Za-z_][A-Za-z0-9_]*$/ { print; next }
      /^[[:space:]]*typeset[[:space:]]+-gx[[:space:]]+[A-Za-z_][A-Za-z0-9_]*=.*/ { print; next }
      /^[[:space:]]*if[[:space:]].*/ { print; next }
      /^[[:space:]]*else[[:space:]]*$/ { print; next }
      /^[[:space:]]*fi[[:space:]]*$/ { print; next }
    ')

    if [ -n "$filtered_out" ]; then
      eval "$filtered_out"
    else
      echo "No environment exports were produced by $cmd." >&2
      return 1
    fi
  fi
  return 0
}

# Command dispatcher
case "$1" in
  awsswitch)
    shift
    run_switch_and_eval awsswitch "$@" || abort_or_return
    ;;
  eksswitch)
    shift
    run_switch_and_eval eksswitch "$@" || abort_or_return
    ;;
  install|remove|uninstall|refresh|configure|healthcheck|help)
    run_python "$@" || abort_or_return
    ;;
  "")
    run_python help || abort_or_return
    ;;
  *)
    echo 'Null or invalid option, run "./awsaccountstools.sh help" for help.'
    abort_or_return
    ;;
esac
