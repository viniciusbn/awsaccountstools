"""CLI entry point for awsaccountstools.

Invoked via: python3 -m awsaccountstools <command> [--emit-shell]

Available commands:
    install       — Add awsswitch/eksswitch functions to your shell profile
    remove        — Remove shell functions from all profile files
    configure     — Interactively set/update SSO configuration
    refresh       — Re-login to SSO and refresh all profiles
    awsswitch     — Switch AWS account, role, and region
    eksswitch     — Switch AWS account + connect to an EKS cluster
    healthcheck   — Run diagnostic checks on configuration and connectivity
    help          — Show usage information
"""

import argparse
import atexit
import sys

from . import commands, config
from . import ui
from .shell import install_tool, remove_tool


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AWS SSO account tools")
    p.add_argument(
        "command",
        choices=[
            "install", "remove", "uninstall", "configure", "refresh",
            "awsswitch", "eksswitch", "healthcheck", "help",
        ],
    )
    p.add_argument(
        "--emit-shell", action="store_true",
        help="Emit shell export commands to stdout",
    )
    p.add_argument(
        "action", nargs="?",
        help="Optional action. For awsswitch/eksswitch use: configure",
    )
    return p


def main() -> int:
    atexit.register(ui.close_ui)
    parser = build_parser()
    if hasattr(parser, "parse_intermixed_args"):
        args = parser.parse_intermixed_args()
    else:
        args = parser.parse_args()

    if args.command == "help":
        parser.print_help()
        return 0

    if args.action and args.command not in {"awsswitch", "eksswitch"}:
        ui.msg_error(f"Unexpected action '{args.action}' for command '{args.command}'.")
        return 1

    if args.action and args.action != "configure":
        ui.msg_error(f"Unsupported action '{args.action}'. Use 'configure'.")
        return 1

    if args.command in {"remove", "uninstall"}:
        return 0 if remove_tool() else 1

    cfg = config.load_env_config()

    if args.command in {"install", "configure", "refresh", "awsswitch", "eksswitch"}:
        try:
            env_local_existed = config.env_local_exists()
            cfg = config.ensure_env_local(cfg)
            should_prompt_config = (
                args.command == "configure"
                or (args.command == "install" and not env_local_existed)
                or (args.command in {"awsswitch", "eksswitch"} and args.action == "configure")
            )
            if should_prompt_config:
                cfg = config.prompt_config_values(cfg, require_interactive=True)
            companies = config.load_companies(cfg)
            if args.command in {"install", "configure", "refresh"}:
                config.check_companies_config(companies)

            if len(companies) == 1:
                ui.set_company_name(companies[0].get("awsCompanyName", "My Company"))
            else:
                ui.set_company_name("Multi Company")
        except Exception as exc:
            ui.msg_error(str(exc))
            return 1

    if args.command == "healthcheck":
        companies = config.load_companies(cfg)
        if len(companies) == 1:
            ui.set_company_name(companies[0].get("awsCompanyName", "My Company"))
        else:
            ui.set_company_name("Multi Company")
        return 0 if commands.do_healthcheck(cfg) else 1

    if args.command in {"install", "configure", "refresh", "awsswitch", "eksswitch"}:
        if not config.require_aws_cli():
            return 1

    if args.command == "install":
        return 0 if install_tool() else 1

    if args.command in {"configure", "refresh"}:
        return 0 if commands.do_configure(cfg) else 1

    if args.command in {"awsswitch", "eksswitch"} and args.action == "configure":
        ui.msg_info("Running configure routine before switch...")
        if not commands.do_configure(cfg):
            return 1
        cfg = config.load_env_config()

    if args.command == "awsswitch":
        return commands.do_awsswitch(cfg, args.emit_shell)

    if args.command == "eksswitch":
        return commands.do_eksswitch(cfg, args.emit_shell)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
