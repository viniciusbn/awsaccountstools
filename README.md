# AWS SSO accounts tools
This script was designed to auto populate AWS Organizations Accounts and create console functions to optimize tasks.

## How to use

1. Clone this repo on any folder.

2. Set your default variable values on .env file.

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
    Switch AWS Account.

    * eksswitch<br />
    Switch EKS Cluster.

    * help<br />
    Show help.

## After you installed this tool

You can run these commands on any new console.

```
awsswitch
```
List the organization accounts to select one.
```
eksswitch
```
List the organization accounts to select one, then list K8s clusters on this account.

## What this tool do on your system?

This tool will modify your ~/.aws/config file to add the required sections and populate it with all profile accounts from your organization.

When you install this app, the script will create functions on your shell profile, to allow you to call these tools from any new console shell.

