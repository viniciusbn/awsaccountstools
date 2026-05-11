# AWS SSO accounts tools
This script was designed to auto populate AWS Organizations Accounts and create console functions to optimize tasks.

## Config files

The repository includes a [.env](.env) file as a **template** with detailed documentation and placeholder values.

**For real usage:**
1. Copy [.env](.env) to `.env.local` (this file is ignored by git).
2. Edit `.env.local` with your organization's actual AWS SSO values.

Alternatively, on first execution without `.env.local`, the script prompts you to generate it interactively from the template.

This approach ensures:
- Template is always tracked and documented in the repo
- Sensitive values in `.env.local` are never accidentally committed
- Easy onboarding for new team members (they follow the template)

## How to use

1. Clone this repo on any folder.

2. Create your `.env.local` configuration:
   ```bash
   cp .env .env.local
   # Then edit .env.local with your organization's AWS SSO values
   ```
   
   Or let the script generate it interactively:
   ```bash
   ./awsaccountstools.sh awsswitch  # Will prompt to generate .env.local on first run
   ```

3. Run this script with one of these options
```
./awsaccountstools.sh OPTION
```


* OPTIONS:
    * install<br />
    Install the AWS Account Tools.

    * remove, uninstall<br />
    Uninstall the AWS Account Tools.

    * refresh, configure<br />
    Configure the AWS Account Tools.

    * awsswitch<br />
    Switch AWS Account and select role dynamically.

    * eksswitch<br />
    Switch AWS Account + role, then switch EKS Cluster.

    * help<br />
    Show help.

## After you installed this tool

You can run these commands on any new console.

```
awsswitch
```
List the organization accounts to select one.
When an account has multiple roles assigned to your user, the tool asks which role you want to use.
The selected account/role profile is created automatically if it does not exist yet.
```
eksswitch
```
List the organization accounts to select one, then list K8s clusters on this account.

> [!WARNING]
> After you have installed this tool, you can't move or delete the local repo, because the functions will point to this location, if you move the folder, rerun the installation, to update the path for functions.

## What does this tool do on your system?

This tool will modify your ~/.aws/config file to add the required sections and populate it with all profile accounts from your organization.

When you install this app, the script will create functions on your shell profile, to allow you to call these tools from any new console shell.

