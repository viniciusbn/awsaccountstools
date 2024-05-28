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
    * install\n
    Install the AWS Account Tools.

    * remove, uninstall\n
    Uninstall the AWS Account Tools.

    * refresh, configure\n
    Configure the AWS Account Tools.

    * awsswitch\n
    Switch AWS Account.

    * eksswitch\n
    Switch EKS Cluster.

    * help\n
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