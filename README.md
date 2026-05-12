# AWS Accounts Tools

Interactive CLI toolkit for switching between AWS SSO accounts, roles, regions, and EKS clusters. Features a full-screen curses TUI with arrow-key navigation, color-coded feedback, and last-selection memory for fast re-use.

## Features

- **AWS SSO integration** — authenticates via `aws sso login` and auto-discovers all accessible accounts and roles
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
| `awsswitch` | Interactive account/role/region selection. Exports `AWS_PROFILE`, `AWS_REGION`, and temporary credentials to the current shell session. |
| `eksswitch` | Same as `awsswitch`, plus EKS cluster selection with automatic kubeconfig generation. |
| `install` | Add `awsswitch`/`eksswitch` functions to your shell profile. |
| `remove` | Remove shell functions from all known profile files. |
| `configure` | Interactively review and update your SSO configuration. |
| `refresh` | Re-login to SSO and refresh all profiles and region cache. |
| `healthcheck` | Run diagnostic checks: AWS CLI, config, SSO token, regions, accounts. |
| `help` | Show usage information. |

### Usage

```bash
# Via shell functions (after install):
awsswitch
eksswitch

# Via script directly:
source awsaccountstools.sh awsswitch
source awsaccountstools.sh eksswitch
./awsaccountstools.sh healthcheck
./awsaccountstools.sh help
```

> **Note:** `awsswitch` and `eksswitch` must be **sourced** (not executed) so that environment variables take effect in the current shell.

## Configuration

### .env (template)

The [.env](.env) file is a documented template with placeholder values. It is tracked in git and serves as a reference for new team members.

### .env.local (your values)

Your actual configuration goes in `.env.local` (git-ignored). Required keys:

| Key | Description | Example |
|-----|-------------|---------|
| `awsStartURL` | Your organization's AWS SSO start URL | `https://my-org.awsapps.com/start` |
| `awsDefaultSession` | SSO session name (used in AWS CLI config) | `my-org-session` |
| `awsDefaultRegion` | Default AWS region for CLI and SSO operations | `us-east-1` |
| `awsCompanyName` | Company name displayed in the TUI header | `My Company` |

The tool also stores last-selection cache entries in `.env.local`:
`lastAccountId`, `lastAccountName`, `lastRoleName`, `lastProfile`, `lastRegion`, `lastCluster`

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

- **`~/.aws/config`** — Adds an `[sso-session ...]` section and `[profile ...]` sections for each account/role combination found via SSO
- **Shell profile** (`~/.zshrc` or `~/.bashrc`) — Adds `awsswitch()` and `eksswitch()` function definitions (on install)
- **`~/.kube/config-*`** — Creates per-cluster kubeconfig files when using `eksswitch`
- **`.env.local`** — Stores your configuration and last-selection cache (local, git-ignored)
- **`.aws_regions`** — Caches the AWS region list locally (git-ignored)

> [!WARNING]
> After installing, do not move or delete the repository folder. The shell functions point to this location. If you move the folder, re-run `install` to update the paths.

## Uninstall

```bash
source awsaccountstools.sh remove
```

This removes the `awsswitch` and `eksswitch` functions from all shell profile files.
