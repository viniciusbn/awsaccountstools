"""AWS Accounts Tools — CLI toolkit for AWS SSO account and EKS cluster switching.

This package provides an interactive terminal UI (curses-based) for switching
between AWS SSO accounts, roles, regions, and EKS clusters. It auto-populates
AWS CLI profiles from your organization's SSO instance and persists the last
selection for fast re-use.

Modules:
    config   — Configuration file management (.env/.env.local), validation
    ui       — Curses TUI rendering, messaging, interactive menus and prompts
    aws      — SSO authentication, profile creation, AWS API calls
    regions  — AWS region fetching, caching, validation, and selection
    shell    — Shell export generation, tool install/uninstall
    commands — Top-level command implementations (awsswitch, eksswitch, etc.)
    utils    — Pure utility functions (sanitize, quote, parse, sort)
"""
