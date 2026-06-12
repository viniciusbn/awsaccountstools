# AWS Accounts Tools

Interactive CLI toolkit for switching between AWS SSO accounts, roles, regions, and EKS clusters across multiple companies. Features a full-screen curses TUI with arrow-key navigation, color-coded feedback, and last-selection memory for fast re-use.

Current documentation target: **2.1.2**

## Features

- **AWS SSO integration** — tries credential reuse/export first and falls back to `aws sso login` only when required
- **Multi-company support** — configure multiple companies, each with its own SSO URL, session, and default region
- **Others mode** — choose external profiles from `~/.aws/config` that are not managed by this app
- **Interactive TUI** — full-screen curses menus with arrow-key navigation, company branding, and color-coded status messages
- **Managed profile cache mode** — daily switching uses existing managed profiles from `~/.aws/config` (no account/role API refresh by default)
- **Explicit refresh flow** — account/role discovery and profile generation run only when you choose refresh
- **Profile ranking** — last-selected profile is always first, followed by most-used profiles, then alphabetical tie-breaks
- **Session-name safety checks** — rejects duplicated company sessions that differ only by letter case
- **Region selection** — choose from default, last-used, or type a custom region (validated against the AWS region list)
- **EKS cluster switching** — select an EKS cluster after choosing an account, auto-generates kubeconfig
- **Last-selection memory** — remembers your last account, role, region, and cluster for faster re-selection
- **`last` shortcut** — `awsswitch last` and `eksswitch last` re-apply the most recent selection without any prompts
- **Shell prompt integration** — shows the active account/role in your zsh RPROMPT or bash PS1
- **Healthcheck** — diagnostic command to verify AWS CLI, SSO token, config, and connectivity
- **Dynamic region cache** — fetches and caches the full AWS region list locally for offline validation
- **Fallback mode** — falls back to a numbered text menu when curses is unavailable

## Prerequisites

- **Python 3.10+** (standard library only — no pip dependencies)
- **AWS CLI v2** (configured with SSO)
- **Bash** or **Zsh** shell

## Setup

All setup operations are performed via `awsaccountstools.sh` directly.

### 1. Clone the repository

```bash
git clone https://github.com/viniciusbn/awsaccountstools.git
cd awsaccountstools
```

### 2. Install shell functions

```bash
source awsaccountstools.sh install
```

This adds `awsswitch` and `eksswitch` functions to your shell profile (`~/.zshrc` or `~/.bashrc`). Reload your shell afterward:

```bash
source ~/.zshrc   # or source ~/.bashrc
```

On first use, the tool will automatically create `.env.local` and guide you through the interactive configuration.

### Other setup commands

| Command | Description |
|---------|-------------|
| `source awsaccountstools.sh install` | Add `awsswitch`/`eksswitch` functions to your shell profile. |
| `source awsaccountstools.sh remove` | Remove shell functions from all known profile files. |
| `./awsaccountstools.sh configure` | Open the interactive configure menu (add, edit, remove companies). |
| `./awsaccountstools.sh refresh` | Re-login to SSO and regenerate managed profiles and region cache (explicit/manual refresh). |
| `./awsaccountstools.sh healthcheck` | Run diagnostic checks and a dry-run cleanup preview. |
| `./awsaccountstools.sh help` | Show usage information. |

> **Note:** `install` and `remove` must be **sourced** to take effect in the current shell.

## Daily Use

After installation, all daily operations use the shell functions directly:

```bash
awsswitch                # Switch AWS account, role, and region
eksswitch                # Switch account + connect to an EKS cluster
awsswitch configure      # Edit companies, then switch
eksswitch configure      # Edit companies, then switch + EKS
awsswitch last           # Re-apply the last selection (no prompts)
eksswitch last           # Re-apply the last EKS selection (no prompts)
```

| Command | Description |
|---------|-------------|
| `awsswitch` | Choose a company (or Others), then select a cached profile and region. |
| `eksswitch` | Same as `awsswitch`, plus EKS cluster selection with automatic kubeconfig generation. |
| `awsswitch configure` | Open the interactive configure menu, then proceed with account switch. |
| `eksswitch configure` | Open the interactive configure menu, then proceed with EKS switch. |
| `awsswitch last` | Re-apply the last `awsswitch` selection (profile + region) without any prompts. Tries cached/refreshable credentials first and falls back to interactive SSO login only if required. |
| `eksswitch last` | Re-apply the last `eksswitch` selection (profile + region + cluster) without any prompts. |

> **Note:** The shell functions set environment variables (`AWS_PROFILE`, `AWS_REGION`, etc.) in your current session — that's why they must run as functions, not as standalone scripts.

> **Tip:** `last` reuses the cache stored in `.env.local` from your previous successful switch. If the profile or cluster is no longer available, run `awsswitch` / `eksswitch` to pick a new one.

### Managed Profiles and Refresh Policy

- Managed company switching reads profiles already present in `~/.aws/config` for the selected `sso_session`.
- Managed company switching is credential-first: it tries `aws configure export-credentials` before attempting interactive SSO re-login.
- The tool does not refresh account/role discovery automatically during normal switching.
- Use `Refresh/Reconfigure Profiles` in the menu or run `./awsaccountstools.sh refresh` when you want to rebuild managed profiles.
- External profiles under Others are also sorted with the same ranking policy (last-selected first, then most-used).

## Configuration

All configuration is managed interactively — there is no need to edit files manually. The tool stores its state as JSON in `.env.local` (git-ignored), which is created automatically on first run.

### Interactive Configure Menu

Running `configure` (or `awsswitch configure` / `eksswitch configure`) opens a menu-driven flow:

1. **Add/Configure companies** — list existing companies for editing, or add a new one via a full-screen editor
2. **Remove companies** — select a company to remove with confirmation; orphaned `[sso-session]` and `[profile]` blocks are automatically cleaned from `~/.aws/config`
3. **Save and continue** — persist changes to `.env.local`

You can configure zero companies to reset the managed configuration (the tool still works with Others/external profiles).

### Company Editor

Each company has four fields edited in a full-screen form:

| Field | Description | Example |
|-------|-------------|---------|
| Company name | Display name in menus and TUI header | `My Company` |
| Start URL | AWS SSO start URL | `https://my-org.awsapps.com/start` |
| Default session | SSO session name (used in AWS CLI config) | `my-org-session` |
| Default region | Default AWS region for CLI and SSO | `us-east-1` |

> **Validation rule:** Session names that differ only by letter case (for example, `Matera-session` and `matera-session`) are treated as duplicates and are not allowed.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release notes.

## Project Structure

```
awsaccountstools/
├── awsaccountstools.sh          # Bash wrapper — sources exports into current shell
├── .env                         # Configuration template (tracked in git)
├── .gitignore
├── README.md
└── awsaccountstools/            # Python package
    ├── __init__.py              # Package description
    ├── __main__.py              # CLI entry point and argument parsing
    ├── utils.py                 # Pure utility functions (sanitize, quote, parse)
    ├── ui.py                    # Curses TUI class, menus, messaging, prompts
    ├── config.py                # .env file management, validation, persistence
    ├── regions.py               # Region fetching, caching, validation, selection
    ├── aws.py                   # SSO authentication, profiles, AWS API calls
    ├── shell.py                 # Shell export generation, install/uninstall
    └── commands.py              # Command orchestration (awsswitch, eksswitch, etc.)
```

## What This Tool Does on Your System

- **`~/.aws/config`** — Adds `[sso-session ...]` and `[profile ...]` sections for each account/role found via SSO. When a company is removed, its associated sections are automatically cleaned up.
- **Shell profile** (`~/.zshrc` or `~/.bashrc`) — Adds `awsswitch()` and `eksswitch()` function definitions (on install)
- **`~/.kube/config-*`** — Creates per-cluster kubeconfig files when using `eksswitch`
- **`.env.local`** — Stores your configuration and last-selection cache (local, git-ignored)
- **`.aws_regions`** — Caches the AWS region list locally (git-ignored)

> [!WARNING]
> After installing, do not move or delete the repository folder. The shell functions point to this location. If you move the folder, re-run `install` to update the paths.

## Healthcheck

The `healthcheck` command runs diagnostic checks and includes a **dry-run cleanup preview** showing how many `[sso-session]` and `[profile]` blocks would be removed from `~/.aws/config` if each managed session were deleted:

```
INFO  Dry-run cleanup preview (if a company/session is removed):
INFO    session 'my-session': 1 sso-session block(s), 12 profile block(s)
```

## Uninstall

```bash
source awsaccountstools.sh remove
```

This removes the `awsswitch` and `eksswitch` functions from all shell profile files (`~/.zshrc`, `~/.bashrc`, `~/.bash_profile`, `~/.zprofile`, `~/.profile`).
