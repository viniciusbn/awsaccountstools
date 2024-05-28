#!/bin/bash

# Default variables

# Default AWS user config file
awsConf=~/.aws/config
awsDefaultSSORegistrationScopes="sso:account:access"
# Get the current APP directory
APP_DIR=$(pwd)
APP_FILE_NAME="$(basename "$0" 2>/dev/null)"
# Import .env variables
source ./.env

# Check if 'jq' is installed.
checkjq () {
    # Check if 'jq' is not installed (its command is not found in the system).
    if [[ -z $(command -v jq) ]]; then
        echo -e "\033[0;33m"
        echo -e "The jq is not installed and is pre-req for this tool.\n"
        echo -e "\033[0m"
        return 1
    else
        return 0
    fi
}

# Check if AWS CLI is installed.
checkAWScli () {
    # Check if 'jq' is not installed (its command is not found in the system).
    if [[ -z $(command -v aws) ]]; then
        echo -e "\033[0;33m"
        echo -e "The AWS CLI is not installed and is pre-req for this tool.\n"
        echo -e "\033[0m"
        return 1
    else
        return 0
    fi
}

# Check if .env file exists.
checkenvfile () {
    if [ -f "$APP_DIR/.env" ]; then
        if [[ -n "$awsStartURL" && -n "$awsDefaultSession" && -n "$awsDefaultProfile" && -n "$awsDefaultSSORole" && -n "$awsDefaultRegion" ]]; then
            return 0
        else
            echo -e "\033[0;33m"
            echo -e "Some of the required variables are not set.\n"
            echo -e "You must set the variables in the .env file.\n"
            echo -e "awsStartURL=$awsStartURL"
            echo -e "awsDefaultSession=$awsDefaultSession"
            echo -e "awsDefaultProfile=$awsDefaultProfile"
            echo -e "awsDefaultSSORole=$awsDefaultSSORole"
            echo -e "awsDefaultRegion=$awsDefaultRegion"
            echo -e "awsDefaultProfileAccountId=$awsDefaultProfileAccountId"
            echo -e "\033[0m"
            return 1
        fi
    else
        echo -e "\033[0;33m"
        echo -e "You must create a .env file as described in the README.\n"
        echo -e "\033[0m"
        return 1
    fi
}

checkAWSSSOsession () {
    aws sts get-caller-identity --profile $awsDefaultProfile &> /dev/null
    if [ $? -eq 0 ]; then
        return 0
    else
        echo -e "\033[0;33m"
        echo -e "The SSO session for profile $awsDefaultProfile is not connected."
        echo -e "Connecting....."
        echo -e "\033[0m"
        aws sso login --profile $awsDefaultProfile
        if [ $? -eq 255 ]; then
            configureAWSFirstConnect
            aws sso login --profile $awsDefaultProfile
            createAWSprofiles
        fi
    fi
}

configureAWSFirstConnect () {
    if ! grep -q "\[sso-session $awsDefaultSession\]" "$awsConf" || ! grep -q "\[profile $awsDefaultProfile\]" "$awsConf"; then
        echo -n > $awsConf.tmp
        if ! grep -q "\[sso-session $awsDefaultSession\]" "$awsConf"; then
            echo -e "\n[sso-session $awsDefaultSession]" >> $awsConf.tmp
            echo "sso_start_url = $awsStartURL" >> $awsConf.tmp
            echo "sso_region = $awsDefaultRegion" >> $awsConf.tmp
            echo -e "\nThe required SSO session was created $awsDefaultSession.\n"
        fi
        if ! grep -q "\[profile $awsDefaultProfile\]" "$awsConf"; then
            echo -e "\n[profile $awsDefaultProfile]" >> $awsConf.tmp
            echo "sso_session = $awsDefaultSession" >> $awsConf.tmp
            echo "sso_account_id = $awsDefaultProfileAccountId" >> $awsConf.tmp
            echo "sso_role_name = $awsDefaultSSORole" >> $awsConf.tmp
            echo "region = $awsDefaultRegion" >> $awsConf.tmp
            echo -e "\nThe required profile was created $awsDefaultProfile.\n"
        fi
        cat $awsConf.tmp >> $awsConf && rm $awsConf.tmp
        sed -i '' '/^[[:space:]]*$/d' $awsConf
    fi
}
createAWSprofiles () {
    echo -n > $awsConf.tmp
    awsAccounts=$(aws organizations list-accounts --profile $awsDefaultProfile | jq -r '.Accounts [] | select(.Status == "ACTIVE")')
    awsAccountsIds=$(echo $awsAccounts | jq -r '.Id')
    echo "$awsAccountsIds" | while IFS= read -r accountId; do
        if ! grep -q "sso_account_id = $accountId" $awsConf; then
            echo -e "\n[profile $(echo $awsAccounts | jq -r --arg accountId "$accountId" 'select(.Id == $accountId) | .Name' | sed 's/ /-/g')]\n" >> $awsConf.tmp
            echo "sso_session = $awsDefaultSession" >> $awsConf.tmp
            echo "sso_account_id = $accountId" >> $awsConf.tmp
            echo "sso_role_name = $awsDefaultSSORole" >> $awsConf.tmp
            echo "region = $awsDefaultRegion" >> $awsConf.tmp
            echo "New account added: $(echo $awsAccounts | jq -r --arg accountId "$accountId" 'select(.Id == $accountId) | .Name' | sed 's/ /-/g')"
        fi
    done
    cat $awsConf.tmp >> $awsConf && rm $awsConf.tmp
    sed -i '' '/^[[:space:]]*$/d' $awsConf
}

selectAWSProfile () {
    while true; do
        # Get the profile list, including 'Exit'
        profiles=("Exit" "Clear" "Refresh" $(aws configure list-profiles | grep -v 'default' | sort | awk '{print $1}'))
        # Display the list of profiles
        PS3="Enter a profile number, 1 to exit, 2 to clear or 3 to refresh the profile selection or list: "
        select profile in "${profiles[@]}"
        do
            if [[ $profile == "Exit" ]]; then
                echo -e "\nExiting...\n"
                return 1
                break 2
            elif [[ $profile == "Clear" ]]; then
                unset AWS_PROFILE
                unset AWS_ACCESS_KEY_ID
                unset AWS_SECRET_ACCESS_KEY
                unset AWS_SESSION_TOKEN
                unset AWS_CREDENTIAL_EXPIRATION
                echo -e "\nCleared... Session profile and credentials was unset.\n"
                return 1
                break 2
            elif [[ $profile == "Refresh" ]]; then
                createAWSprofiles
                clear
                break
            else
                echo -e "\nSelected profile: $profile\n"
                echo -e "Programmatic credentials for profile $profile are defined.\n"
                export AWS_PROFILE=$profile
                #Get AWS programmatic credentials for CLI.
                eval "$(aws configure export-credentials --profile $profile --format env)"
                break 2
            fi
        done
    done
}

selectEKScluster () {
    if [ -z "$AWS_PROFILE" ]; then
        echo -e "\n\nNone profile selected.\n\n"
    else
        # Get the profile list, including 'Exit'
        eks_clusters=("Exit" $(aws eks list-clusters --profile $AWS_PROFILE --query "clusters[]" --output json | jq -r '.[]'))
        PS3="Select the EKS Cluster to use or 1 to 'Exit': "
        echo -e "\nSelec the EKS Cluster:\n"
        # Display the list of profiles
        select eks_cluster in "${eks_clusters[@]}"
        do
            if [[ $eks_cluster == "Exit" ]]; then
                echo -e "\nExiting...\n"
                break
            else
                echo -e "\nSelected eks: $eks_cluster\nConnecting...\n"
                aws eks update-kubeconfig --name $eks_cluster
                break
            fi
        done
    fi
}

installTool () {
    ALIASES_APP_NAME="awsswitch\neksswitch"
    # Get the origin APP name script file.
    # Determine the alias file based on the user's shell (zsh, bash, or sh)
    case $(basename $SHELL) in
        zsh)
            ALIAS_FILE=~/.zprofile
            ;;
        bash)
            ALIAS_FILE=~/.bash_profile
            ;;
        sh)
            ALIAS_FILE=~/.profile
            ;;
    esac
    echo -e "$ALIAS_APP_NAME" | while read -r ALIAS_NAME; do
        # Check if the alias/function already exists in the alias file.
        if grep -q "function $ALIAS_NAME" $ALIAS_FILE 2>/dev/null; then
            # Update the alias/function if it exists.
            sed -i.bak "/^function $ALIAS_NAME() {/,/^}$/d" "$ALIAS_FILE"
            echo -e "function $ALIAS_NAME() {\n\tsource $APP_DIR/$APP_FILE_NAME $ALIAS_NAME\n}" >> "$ALIAS_FILE"
        else
            # Create a new alias/function if it doesn't exist.
            echo -e >> "$ALIAS_FILE"
            echo -e "function $ALIAS_NAME() {\n\tsource $APP_DIR/$APP_FILE_NAME $ALIAS_NAME\n}" >> "$ALIAS_FILE"
        fi
    done
    # Reload your shell profile.
    echo -e "\nReload your shell profile using the command: source $ALIAS_FILE or open a new console\n"
}

removeTool () {
    ALIASES_APP_NAME="awsswitch\neksswitch"
    # Get the origin APP name script file.
    # Determine the alias file based on the user's shell (zsh, bash, or sh)
    case $(basename $SHELL) in
        zsh)
            ALIAS_FILE=~/.zprofile
            ;;
        bash)
            ALIAS_FILE=~/.bash_profile
            ;;
        sh)
            ALIAS_FILE=~/.profile
            ;;
    esac
    echo -e "$ALIAS_APP_NAME" | while read -r ALIAS_NAME; do
        # Uninstall by removing the script alias/function from shell profile.
        sed -i.bak "/^function $ALIAS_NAME() {/,/^}$/d" "$ALIAS_FILE" 
    done
}

#Check for pre-reqs.
if checkjq && checkAWScli && checkenvfile; then
    case $1 in
        install)
            #install script
            echo "Installing AWS Account Tools..."
            installTool
            ;;
        remove|uninstall)
            #uninstall script
            echo "Uninstalling AWS Account Tools..."
            removeTool
            ;;
        refresh|configure)
            #refresh accounts
            echo "Configuring AWS Account..."
            checkAWSSSOsession
            configureAWSFirstConnect
            createAWSprofiles
            ;;
        awsswitch)
            #switch aws accounts
            echo "Switching AWS Account..."
            checkAWSSSOsession
            selectAWSProfile
            ;;
        eksswitch)
            #switch eks accounts
            echo "Switching EKS Cluster..."
            checkAWSSSOsession
            selectAWSProfile
            if [ $? -ne 1 ]; then
                selectEKScluster
            fi
            ;;
        help)
            #show help
            echo "AWS Account Tools Help"
            ;;
        *)
            echo "Null option or invalid option, check documentation for help."
            ;;
    esac
fi