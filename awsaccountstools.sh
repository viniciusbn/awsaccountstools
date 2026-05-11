#!/usr/bin/env bash

if [ -n "$BASH_VERSION" ]; then
  APP_DIR=$(dirname "$(realpath "$BASH_SOURCE")")
else
  APP_DIR=$(dirname "$(realpath "$0")")
fi
PY_APP="$APP_DIR/awsaccountstools.py"

abort_or_return() {
  return 1 2>/dev/null || exit 1
}

run_python() {
  if command -v python3 >/dev/null 2>&1; then
    python3 "$PY_APP" "$@"
  else
    echo "python3 is required to run awsaccountstools." >&2
    return 1
  fi
}

run_switch_and_eval() {
  local cmd="$1"
  shift
  local shell_out
  local filtered_out

  shell_out=$(run_python "$cmd" --emit-shell "$@") || return 1
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

case "$1" in
  awsswitch)
    shift
    run_switch_and_eval awsswitch "$@" || abort_or_return
    ;;
  eksswitch)
    shift
    run_switch_and_eval eksswitch "$@" || abort_or_return
    ;;
  install|remove|uninstall|refresh|configure|help)
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
