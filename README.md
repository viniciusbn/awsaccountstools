# AWS Accounts Tools

Interactive CLI toolkit for switching between AWS SSO accounts, roles, regions, and EKS clusters across multiple companies. Features a full-screen curses TUI with arrow-key navigation, color-coded feedback, and last-selection memory for fast re-use.

## Features

- **AWS SSO integration** — authenticates via `aws sso login` and auto-discovers all accessible accounts and roles
- **Multi-company support** — configure multiple companies, each with its own SSO URL, session, and default region
- **Others mode** — choose external profiles from `~/.aws/config` that are not managed by this app
- **Interactive TUI** — full-screen curses menus with arrow-key navigation, company branding, and color-coded status messages
- **Auto profile creation** — automatically generates `[profile ...]` sections in `~/.aws/config` for every account/role combination
- **Region selection** — choose from default, last-used, or type a custom region (validated against the AWS region list)
- **EKS cluster switching** — select an EKS cluster after choosing an account, auto-generates kubeconfig
- **Last-selection memory** — remembers your last account, role, region, and cluster for faster re-selection
- **Shell prompt integration** — shows the active account/role in your zsh RPROMPT or bash PS1
- **Healthcheck** — diagnostic command to verify AWS CLI, SSO token, config, and connectivity
- **Dynamic region cache** — fetches and caches the full AWS region list locally for offline validation
- **Fallback mode** — falls back to a numbered text menu when curses is unavailable

## Prerequisites

- **Python 3.10+** (standard library only — no pip dependencies)
- **AWS CLI v2** (configured with SSO)
- **Bash** or **Zsh** shell

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/viniciusbn/awsaccountstools.git
cd awsaccountstools
```

### 2. Create your configuration

Copy the template and fill in your organization's values:

```bash
cp .env .env.local
# Edit .env.local with your AWS SSO URL, session name, region, and company name
```

Or let the tool generate it interactively on first run:

```bash
source awsaccountstools.sh awsswitch
# → Will prompt to create .env.local if it doesn't exist
```

### 3. Install shell functions

```bash
source awsaccountstools.sh install
```

This adds `awsswitch` and `eksswitch` functions to your shell profile (`~/.zshrc` or `~/.bashrc`). Reload your shell afterward:

```bash
source ~/.zshrc   # or source ~/.bashrc
```

### 4. Use it

```bash
awsswitch    # Switch AWS account, role, and region
eksswitch    # Switch account + connect to an EKS cluster
```

## Commands

| Command | Description |
|---------|-------------|
| `awsswitch` | Choose a company (or Others), then interactively select account/role/region. |
| `eksswitch` | Same as `awsswitch`, plus EKS cluster selection with automatic kubeconfig generation. |
| `awsswitch configure` | Open the interactive configure menu, then proceed with account switch. |
| `eksswitch configure` | Open the interactive configure menu, then proceed with EKS switch. |
| `install` | Add `awsswitch`/`eksswitch` functions to your shell profile. |
| `remove` | Remove shell functions from all known profile files. |
| `configure` | Open the interactive configure menu (add, edit, remove companies). |
| `refresh` | Re-login to SSO and refresh all profiles and region cache. |
| `healthcheck` | Run diagnostic checks and a dry-run cleanup preview. |
| `help` | Show usage information. |

### Usage

```bash
# Via shell functions (after install):
awsswitch
eksswitch
awsswitch configure    # Edit companies, then switch
eksswitch configure    # Edit companies, then switch + EKS

# Via script directly:
source awsaccountstools.sh awsswitch
source awsaccountstools.sh eksswitch
./awsaccountstools.sh configure
./awsaccountstools.sh healthcheck
./awsaccountstools.sh help
```

> **Note:** `awsswitch` and `eksswitch` must be **sourced** (not executed) so that environment variables take effect in the current shell.

## Configuration

Configuration is stored as JSON in `.env.local` (git-ignored). On first run, the tool will prompt you to create it.

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

This removes the `awsswitch` and `eksswitch` functions from all shell profile files.
