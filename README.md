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
        * install
        Install the AWS Account Tools.

        * remove, uninstall
        Uninstall the AWS Account Tools.

        * refresh, configure
        Configure the AWS Account Tools.

        * awsswitch
        Switch AWS Account.

        * eksswitch
        Switch EKS Cluster.

        * help
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