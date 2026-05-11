#!/bin/bash

# Default variables

# Default AWS user config file
awsConf=~/.aws/config
awsDefaultSSORegistrationScopes="sso:account:access"

# Get the current APP directory
if [ -n "$BASH_VERSION" ]; then
  APP_DIR=$(dirname "$(realpath "$BASH_SOURCE")")
else
  APP_DIR=$(dirname "$(realpath "$0")")
fi
APP_FILE_NAME="$(basename "$0" 2>/dev/null)"

setEnvFile () {
    if [ -f "$APP_DIR/.env.local" ]; then
        ENV_FILE="$APP_DIR/.env.local"
    else
        ENV_FILE="$APP_DIR/.env"
    fi
}

loadEnvFile () {
    setEnvFile
    if [ -f "$ENV_FILE" ]; then
        # shellcheck disable=SC1090
        source "$ENV_FILE"
    fi
}

getTemplateValue () {
    local varName="$1"
    local fallbackValue="$2"
    local templateValue

    templateValue=$(grep -E "^${varName}=" "$APP_DIR/.env" 2>/dev/null | tail -n 1 | sed -E 's/^[^=]+=//; s/^"//; s/"$//')
    if [ -n "$templateValue" ]; then
        echo "$templateValue"
    else
        echo "$fallbackValue"
    fi
}

promptValueOrDefault () {
    local label="$1"
    local defaultValue="$2"
    local typedValue

    read -r -p "$label [$defaultValue]: " typedValue
    if [ -n "$typedValue" ]; then
        echo "$typedValue"
    else
        echo "$defaultValue"
    fi
}

promptRequiredValue () {
    local label="$1"
    local defaultValue="$2"
    local typedValue

    while true; do
        read -r -p "$label [$defaultValue]: " typedValue
        if [ -n "$typedValue" ]; then
            echo "$typedValue"
            return 0
        fi
        if [ -n "$defaultValue" ]; then
            echo "$defaultValue"
            return 0
        fi
        echo "This field is required."
    done
}

bootstrapEnvLocalFromTemplate () {
    local startURL
    local defaultSession
    local defaultRegion

    if [ -f "$APP_DIR/.env.local" ]; then
        return 0
    fi

    if [ ! -t 0 ]; then
        echo -e "\033[0;33m"
        echo -e "No .env.local found. Open an interactive shell to create it automatically.\n"
        echo -e "\033[0m"
        return 1
    fi

    echo -e "\nNo .env.local was found."
    echo -e "Creating .env.local and prompting required values...\n"

    startURL=$(promptRequiredValue "awsStartURL" "$(getTemplateValue "awsStartURL" "$awsStartURL")")
    defaultSession=$(promptRequiredValue "awsDefaultSession" "$(getTemplateValue "awsDefaultSession" "$awsDefaultSession")")
    defaultRegion=$(promptRequiredValue "awsDefaultRegion" "$(getTemplateValue "awsDefaultRegion" "$awsDefaultRegion")")

    cat > "$APP_DIR/.env.local" <<EOF
# Local runtime configuration. Keep this file out of version control.
awsStartURL="$startURL"
awsDefaultSession="$defaultSession"
awsDefaultRegion="$defaultRegion"
EOF

    chmod 600 "$APP_DIR/.env.local" 2>/dev/null
    echo -e "\nCreated .env.local successfully.\n"

    loadEnvFile
    return 0
}

# Import env variables
loadEnvFile

stripEmptyLines () {
    local file="$1"
    if sed --version >/dev/null 2>&1; then
        sed -i '/^[[:space:]]*$/d' "$file"
    else
        sed -i '' '/^[[:space:]]*$/d' "$file"
    fi
}

sanitizeName () {
    echo "$1" | tr '[:space:]/' '--' | tr -cd '[:alnum:]_.-' | sed 's/--*/-/g' | sed 's/^-//; s/-$//'
}

buildProfileName () {
    local accountName="$1"
    local roleName="$2"
    local accountSlug
    local roleSlug

    accountSlug=$(sanitizeName "$accountName")
    roleSlug=$(sanitizeName "$roleName")
    echo "${accountSlug}-${roleSlug}"
}

isSSOTokenValid () {
    local cacheDir="$HOME/.aws/sso/cache"
    local expiresAt
    local now

    if [ ! -d "$cacheDir" ]; then
        return 1
    fi

    expiresAt=$(jq -rs \
        --arg startUrl "$awsStartURL" \
        'map(select((.startUrl == $startUrl or .startURL == $startUrl) and .accessToken)) | sort_by(.expiresAt // "") | reverse | .[0].expiresAt // empty' \
        "$cacheDir"/*.json 2>/dev/null)

    if [ -z "$expiresAt" ] || [ "$expiresAt" = "null" ]; then
        return 1
    fi

    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    if [[ "$expiresAt" > "$now" ]]; then
        return 0
    fi

    return 1
}

getSSOAccessToken () {
    local cacheDir="$HOME/.aws/sso/cache"
    local token

    if [ ! -d "$cacheDir" ]; then
        return 1
    fi

    token=$(jq -rs \
        --arg startUrl "$awsStartURL" \
        'map(select((.startUrl == $startUrl or .startURL == $startUrl) and .accessToken)) | sort_by(.expiresAt // "") | reverse | .[0].accessToken // empty' \
        "$cacheDir"/*.json 2>/dev/null)

    if [ -z "$token" ] || [ "$token" = "null" ]; then
        return 1
    fi

    echo "$token"
    return 0
}

listAccessibleAccounts () {
    local token
    local result

    token=$(getSSOAccessToken)
    if [ -n "$token" ]; then
        result=$(aws sso list-accounts --access-token "$token" --region "$awsDefaultRegion" --output json 2>&1)
        if [ $? -eq 0 ]; then
            echo "$result" | jq -r '.accountList[]? | "\(.accountId)|\(.accountName)"' | sort -t'|' -k2
            return 0
        fi
    fi

    return 1
}


listAccountRoles () {
    local accountId="$1"
    local token

    token=$(getSSOAccessToken)
    if [ -n "$token" ]; then
        aws sso list-account-roles --access-token "$token" --account-id "$accountId" --region "$awsDefaultRegion" --output json 2>/dev/null |
            jq -r '.roleList[]? | .roleName' | sort
        return 0
    fi

    return 1
}

createProfileIfMissing () {
    local profileName="$1"
    local accountId="$2"
    local roleName="$3"

    ensureAWSConfigFile

    if ! grep -q "\[profile $profileName\]" "$awsConf" 2>/dev/null; then
        {
            echo ""
            echo "[profile $profileName]"
            echo "sso_session = $awsDefaultSession"
            echo "sso_account_id = $accountId"
            echo "sso_role_name = $roleName"
            echo "region = $awsDefaultRegion"
        } >> "$awsConf"
        echo "New account/role profile added: $profileName"
        stripEmptyLines "$awsConf"
    fi
}

ensureAWSConfigFile () {
    mkdir -p "$HOME/.aws"
    touch "$awsConf"
}

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
    if [ ! -f "$APP_DIR/.env.local" ]; then
        bootstrapEnvLocalFromTemplate
        loadEnvFile
    fi

    setEnvFile

    if [ -f "$ENV_FILE" ]; then
        if [[ -n "$awsStartURL" && -n "$awsDefaultSession" && -n "$awsDefaultRegion" ]]; then
            return 0
        else
            echo -e "\033[0;33m"
            echo -e "Some of the required variables are not set.\n"
            echo -e "You must set the variables in the env file: $ENV_FILE\n"
            echo -e "awsStartURL=$awsStartURL"
            echo -e "awsDefaultSession=$awsDefaultSession"
            echo -e "awsDefaultRegion=$awsDefaultRegion"
            echo -e "\033[0m"
            return 1
        fi
    else
        echo -e "\033[0;33m"
        echo -e "You must create a .env.local (recommended) or .env file as described in the README.\n"
        echo -e "\033[0m"
        return 1
    fi
}

checkAWSSSOsession () {
    ensureAWSConfigFile
    configureAWSFirstConnect

    if isSSOTokenValid; then
        return 0
    fi

    echo -e "\033[0;33m"
    echo -e "SSO session '$awsDefaultSession' is not active or expired.\n"
    echo -e "Attempting to authenticate...\n"
    echo -e "\033[0m"
    aws sso login --sso-session "$awsDefaultSession"
    if [ $? -ne 0 ]; then
        echo -e "\033[0;33m"
        echo -e "Could not authenticate. Please ensure:\n"
        echo -e "  1. AWS SSO session '$awsDefaultSession' is configured in ~/.aws/config\n"
        echo -e "  2. You have internet connectivity\n"
        echo -e "\033[0m"
        return 1
    fi
    createAWSprofiles
}

configureAWSFirstConnect () {
    ensureAWSConfigFile

    if ! grep -q "\[sso-session $awsDefaultSession\]" "$awsConf" 2>/dev/null; then
        {
            echo ""
            echo "[sso-session $awsDefaultSession]"
            echo "sso_start_url = $awsStartURL"
            echo "sso_region = $awsDefaultRegion"
            echo "sso_registration_scopes = $awsDefaultSSORegistrationScopes"
        } >> "$awsConf"
        echo -e "\nSSO session configured: $awsDefaultSession.\n"
        stripEmptyLines "$awsConf"
    fi
}
createAWSprofiles () {
    local accounts
    local accountEntry
    local accountId
    local accountName
    local roles
    local roleName
    local profileName

    accounts=$(listAccessibleAccounts)
    if [ -z "$accounts" ]; then
        echo -e "No AWS accounts available for this SSO session/profile.\n"
        return 1
    fi

    while IFS= read -r accountEntry; do
        accountId=$(echo "$accountEntry" | cut -d'|' -f1)
        accountName=$(echo "$accountEntry" | cut -d'|' -f2-)
        roles=$(listAccountRoles "$accountId")

        while IFS= read -r roleName; do
            [ -z "$roleName" ] && continue
            profileName=$(buildProfileName "$accountName" "$roleName")
            createProfileIfMissing "$profileName" "$accountId" "$roleName"
        done <<< "$roles"
    done <<< "$accounts"
}

readMenuKey () {
    local key

    if [ -n "$ZSH_VERSION" ]; then
        read -rsk1 key < /dev/tty
    else
        IFS= read -rsn1 key < /dev/tty
    fi

    printf '%s' "$key"
}

selectFromMenuNative () {
    local title="$1"
    shift
    local options=("$@")
    local selected
    local key key2 key3
    local i
    local indexBase
    local maxIndex
    local menuLines

    if [ ${#options[@]} -eq 0 ]; then
        return 1
    fi

    # zsh arrays are 1-based by default, bash arrays are 0-based.
    if [ -n "$ZSH_VERSION" ]; then
        indexBase=1
    else
        indexBase=0
    fi

    selected=$indexBase
    maxIndex=$(( ${#options[@]} + indexBase - 1 ))

    while true; do
        echo "" >&2
        echo "$title" >&2
        i=$indexBase
        while [ $i -le $maxIndex ]; do
            if [ $i -eq $selected ]; then
                echo " > ${options[$i]}" >&2
            else
                echo "   ${options[$i]}" >&2
            fi
            i=$((i + 1))
        done
        echo "" >&2
        echo "Use Arrow Up/Down and Enter (q to cancel)" >&2

        key=$(readMenuKey)

        if [ -z "$key" ]; then
            echo "${options[$selected]}"
            return 0
        fi

        if [ "$key" = "q" ] || [ "$key" = "Q" ]; then
            return 1
        fi

        if [ "$key" = $'\033' ]; then
            key2=$(readMenuKey)
            key3=$(readMenuKey)
            if [ "$key2" = "[" ]; then
                case "$key3" in
                    A)
                        selected=$((selected - 1))
                        ;;
                    B)
                        selected=$((selected + 1))
                        ;;
                esac
            fi
        fi

        if [ $selected -lt $indexBase ]; then
            selected=$maxIndex
        fi
        if [ $selected -gt $maxIndex ]; then
            selected=$indexBase
        fi

        menuLines=$(( ${#options[@]} + 4 ))
        printf '\033[%sA' "$menuLines" >&2
    done
}

selectAWSProfile () {
    local accounts
    local accountOptions=()
    local accountMap=()
    local accountChoice
    local selectedAccount
    local accountId
    local accountName
    local roles
    local roleOptions=()
    local selectedRole
    local profile
    local loginAttempts=0

    while true; do
        accountMap=()
        while IFS= read -r line; do
            [ -n "$line" ] && accountMap+=("$line")
        done < <(listAccessibleAccounts)
        
        if [ ${#accountMap[@]} -eq 0 ]; then
            if [ $loginAttempts -eq 0 ]; then
                loginAttempts=$((loginAttempts + 1))
                echo -e "\033[0;33m"
                echo -e "No accessible accounts found. Attempting SSO login...\n"
                echo -e "\033[0m"
                aws sso login --sso-session "$awsDefaultSession"
                if [ $? -eq 0 ]; then
                    echo -e "\nSSO login successful. Retrying account list...\n"
                    continue
                fi
            fi
            echo -e "No accessible accounts found.\n"
            return 1
        fi

        accountOptions=("Exit" "Clear" "Refresh")
        for selectedAccount in "${accountMap[@]}"; do
            accountId=$(echo "$selectedAccount" | cut -d'|' -f1)
            accountName=$(echo "$selectedAccount" | cut -d'|' -f2-)
            accountOptions+=("$accountName ($accountId)")
        done

        accountChoice=$(selectFromMenuNative "Select an AWS account:" "${accountOptions[@]}")
        if [ -z "$accountChoice" ]; then
            return 1
        fi

        if [[ "$accountChoice" == "Exit" ]]; then
            echo -e "\nExiting...\n"
            return 1
        elif [[ "$accountChoice" == "Clear" ]]; then
            unset AWS_PROFILE
            unset AWS_ACCESS_KEY_ID
            unset AWS_SECRET_ACCESS_KEY
            unset AWS_SESSION_TOKEN
            unset AWS_CREDENTIAL_EXPIRATION
            unset RPROMPT
            if [ -n "$_ORIG_PS1" ]; then
                export PS1="$_ORIG_PS1"
                unset _ORIG_PS1
            fi
            echo -e "\nCleared... Session profile and credentials were unset.\n"
            return 1
        elif [[ "$accountChoice" == "Refresh" ]]; then
            createAWSprofiles
            clear
            continue
        fi

        selectedAccount=""
        while IFS= read -r line; do
            [ -z "$line" ] && continue
            accountId=$(echo "$line" | cut -d'|' -f1)
            accountName=$(echo "$line" | cut -d'|' -f2-)
            if [ "$accountChoice" = "$accountName ($accountId)" ]; then
                selectedAccount="$line"
                break
            fi
        done <<< "$(printf '%s\n' "${accountMap[@]}")"

        if [ -z "$selectedAccount" ]; then
            echo -e "\nInvalid account selection. Please try again.\n"
            continue
        fi

        accountId=$(echo "$selectedAccount" | cut -d'|' -f1)
        accountName=$(echo "$selectedAccount" | cut -d'|' -f2-)

        roleOptions=()
        while IFS= read -r line; do
            [ -n "$line" ] && roleOptions+=("$line")
        done < <(listAccountRoles "$accountId")
        if [ ${#roleOptions[@]} -eq 0 ]; then
            echo -e "\nNo roles found for account '$accountName'. Skipping.\n"
            continue
        fi

        if [ ${#roleOptions[@]} -eq 1 ]; then
            selectedRole=$(printf '%s\n' "${roleOptions[@]}" | head -n 1)
            echo -e "\nUnique role found for '$accountName': $selectedRole\n"
        else
            roleOptions+=("Exit")
            selectedRole=$(selectFromMenuNative "Select the role for account '$accountName':" "${roleOptions[@]}")
            if [ -z "$selectedRole" ] || [ "$selectedRole" = "Exit" ]; then
                continue
            fi
        fi

        if [ -z "$selectedRole" ]; then
            continue
        fi

        profile=$(buildProfileName "$accountName" "$selectedRole")
        createProfileIfMissing "$profile" "$accountId" "$selectedRole"

        echo -e "\nSelected profile: $profile\n"
        echo -e "Programmatic credentials for profile $profile are defined.\n"

        export AWS_PROFILE="$profile"
        export PROFILE="$profile"
        local promptAccount promptRole
        promptAccount=$(echo "$accountName" | tr '[:space:]' '-' | sed 's/-\{2,\}/-/g; s/^-//; s/-$//')
        promptRole=$(echo "$selectedRole" | tr '[:space:]' '-' | sed 's/-\{2,\}/-/g; s/^-//; s/-$//')
        if [ -n "$ZSH_VERSION" ]; then
            export RPROMPT="%{$fg[blue]%}(ACC:${promptAccount}-R:${promptRole})%{$reset_color%}"
        else
            export _ORIG_PS1="${_ORIG_PS1:-$PS1}"
            export PS1="\[\033[0;34m\](ACC:${promptAccount}-R:${promptRole})\[\033[0m\] $_ORIG_PS1"
        fi

        checkAWSSSOsession
        eval "$(aws configure export-credentials --profile "$profile" --format env)"
        return 0
    done
}

configureEKSconnection() {
    local EKS_CLUSTER="$1"
    echo -e "\nSelected eks: $EKS_CLUSTER\nConnecting...\n"
    export KUBECONFIG=~/.kube/config-"$AWS_PROFILE"-"$EKS_CLUSTER"
    export RPROMPT='%{$fg[blue]%}(EKS: '$EKS_CLUSTER')%{$reset_color%}'
    aws eks update-kubeconfig --name "$EKS_CLUSTER" --profile "$AWS_PROFILE" --kubeconfig "$KUBECONFIG"
}

selectEKScluster () {
    if [ -z "$AWS_PROFILE" ]; then
        echo -e "\n\nNone profile selected.\n\n"
        return 1
    else
        local EKS_CLUSTERS
        EKS_CLUSTERS=$(aws eks list-clusters --profile "$AWS_PROFILE" --query "clusters[]" --output json 2>/dev/null | jq -r '.[]' | sort)
        
        # Count the number of elements in the array
        if [ -z "$EKS_CLUSTERS" ]; then
            echo -e "No EKS clusters found.\n"
            return 1
        fi
        local CLUSTERS_COUNT=$(echo -e "$EKS_CLUSTERS" | wc -l)

        # Check if the array is empty
        if [ "$CLUSTERS_COUNT" -eq 1 ]; then
            echo -e "\nUnique EKS cluster found: $EKS_CLUSTERS\n"
            configureEKSconnection "$EKS_CLUSTERS"
            return 0
        else
            local clusterOptions=("Exit")
            while IFS= read -r cluster; do
                [ -n "$cluster" ] && clusterOptions+=("$cluster")
            done <<< "$EKS_CLUSTERS"

            local selectedCluster
            selectedCluster=$(selectFromMenuNative "Select the EKS Cluster:" "${clusterOptions[@]}")
            if [ -z "$selectedCluster" ] || [ "$selectedCluster" = "Exit" ]; then
                echo -e "\nExiting...\n"
                return 1
            fi

            configureEKSconnection "$selectedCluster"
        fi
    fi
}

installTool () {
    ALIASES_APP_NAME="awsswitch\neksswitch"
    local detectedShell
    local parentShell

    parentShell=$(ps -p "$PPID" -o comm= 2>/dev/null | tr -d '[:space:]')

    # Prefer the current shell context when sourced, then parent shell, then SHELL env var.
    if [ -n "$ZSH_VERSION" ]; then
        detectedShell="zsh"
    elif [ -n "$BASH_VERSION" ] && [ "$parentShell" = "zsh" ]; then
        # Script executed via shebang from a zsh session.
        detectedShell="zsh"
    elif [ "$parentShell" = "zsh" ] || [ "$parentShell" = "bash" ] || [ "$parentShell" = "sh" ]; then
        detectedShell="$parentShell"
    else
        detectedShell=$(basename "$SHELL")
    fi

    case "$detectedShell" in
        zsh)
            if [ -f "$HOME/.zshrc" ] || [ ! -f "$HOME/.zprofile" ]; then
                ALIAS_FILE="$HOME/.zshrc"
            else
                ALIAS_FILE="$HOME/.zprofile"
            fi
            ;;
        bash)
            if [ -f "$HOME/.bashrc" ] || [ ! -f "$HOME/.bash_profile" ]; then
                ALIAS_FILE="$HOME/.bashrc"
            else
                ALIAS_FILE="$HOME/.bash_profile"
            fi
            ;;
        *)
            ALIAS_FILE="$HOME/.profile"
            ;;
    esac

    touch "$ALIAS_FILE"
    echo -e "$ALIASES_APP_NAME" | while read -r ALIAS_NAME; do
        # Check if the alias/function already exists in the alias file.
        if grep -q "function $ALIAS_NAME" "$ALIAS_FILE" 2>/dev/null; then
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
    local shellFiles=("$HOME/.zprofile" "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.bashrc" "$HOME/.profile")
    local targetFile

    for targetFile in "${shellFiles[@]}"; do
        [ -f "$targetFile" ] || continue
        echo -e "$ALIASES_APP_NAME" | while read -r ALIAS_NAME; do
            # Uninstall by removing the script alias/function from shell profile.
            sed -i.bak "/^function $ALIAS_NAME() {/,/^}$/d" "$targetFile"
        done
        rm -f "$targetFile.bak"
    done

    # If this script was sourced in the current shell, remove functions immediately.
    unset -f awsswitch 2>/dev/null
    unset -f eksswitch 2>/dev/null

    echo -e "\nawsaccountstools.sh has been uninstalled.\n"
}

appHelp () {
    echo -e "\nAWS SSO Account Tools Help\n"
    echo -e "Run from local repo directory."
    echo -e "Usage: ./awsaccountstools.sh [OPTION]"
    echo -e "Options:\n"
    echo -e "  install               Install the AWS Account Tools."
    echo -e "  remove, uninstall     Uninstall the AWS Account Tools."
    echo -e "  refresh, configure    Configure the AWS Account Tools."
    echo -e "  awsswitch             Switch AWS Account."
    echo -e "  eksswitch             Switch EKS Cluster."
    echo -e "  help                  Show help."
    echo -e "\n\nAfter installing the AWS Account Tools, you can use it by running the following command, on any new console:\n"
    echo -e "   awsswitch   Switch AWS Account"
    echo -e "   eksswitch   Switch EKS Cluster"
}

abortCommand () {
    return 1 2>/dev/null || exit 1
}

case $1 in
    remove|uninstall)
        # uninstall should work even when env/aws/jq checks fail
        echo "Uninstalling AWS Account Tools..."
        removeTool
        ;;
    help)
        appHelp
        ;;
    install|refresh|configure|awsswitch|eksswitch)
        if checkjq && checkAWScli && checkenvfile; then
            case $1 in
                install)
                    #install script
                    echo "Installing AWS Account Tools..."
                    installTool
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
                    checkAWSSSOsession || abortCommand
                    selectAWSProfile
                    ;;
                eksswitch)
                    #switch eks accounts/clusters
                    checkAWSSSOsession || abortCommand
                    selectAWSProfile
                    if [ $? -ne 1 ]; then
                        selectEKScluster
                    fi
                    ;;
            esac
        fi
        ;;
    *)
        echo 'Null or invalid option, run "./awsaccountstools.sh help" for help.'
        ;;
esac