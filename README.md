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

You can run on any new console these commands

```
awsswitch
```
#Will list the organizations accounts to select one
```
eksswitch
```
#Will list the organizations accotuns to select one, the list K8s clusters one this account.