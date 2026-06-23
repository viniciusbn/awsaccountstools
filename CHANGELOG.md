# Changelog

All notable changes for this project are documented in this file.

## 2.1.3 - 2026-06-22

### Changed

- Credential export now proactively validates the SSO session before calling `aws configure export-credentials`. If the SSO token is expired or missing, `aws sso login` is triggered first, ensuring that `AWS_PROFILE` and the exported STS credentials are always in sync.
- `awsswitch`, `eksswitch`, and their `last` shortcuts will now prompt for browser re-authentication whenever the SSO session has expired, instead of silently exporting stale cached STS credentials that would later cause `InvalidGrantException` errors in tools such as Terraform and Terragrunt.

### Fixed

- Fixed a split-brain authentication state where the SSO token could expire while valid STS credentials still existed in the CLI cache. In that state, tools that give priority to `AWS_PROFILE` (e.g. Terraform's AWS Go SDK) would attempt a profile-based credential refresh, fail with `InvalidGrantException`, and ignore the still-valid environment-variable credentials entirely.
- `awsswitch last` and `eksswitch last` now consistently renew the SSO session when needed, making them safe to call at any point during the day to restore a broken session.

## 2.1.2 - 2026-06-10

### Changed

- Managed profile switching now follows a credential-first flow: the tool tries `aws configure export-credentials` before triggering interactive SSO login.
- `awsswitch`, `eksswitch`, and their `last` shortcuts now only run `aws sso login` as a fallback when credential export fails.
- `Refresh/Reconfigure Profiles` now performs explicit SSO validation before account/role discovery.

### Fixed

- Reduced unnecessary SSO reauthentication prompts during normal daily account/role switching when AWS CLI can reuse or silently refresh session state.

## 2.1.1 - 2026-06-01

### Added

- Managed profile cache selection for company sessions, using existing profiles from ~/.aws/config during normal switching.
- Profile ranking by usage count, with the most recently selected profile always pinned to the first position.
- Persistent profile usage counters in selection cache to improve ordering over time.
- Validation rule that prevents duplicated configured session names that differ only by letter case.

### Changed

- AWS SSO cache token lookup is now more tolerant and resilient by combining start URL matching and session-name-based cache resolution.
- Managed profile detection now compares sso_session values case-insensitively.
- Others profile selection now uses the same ranking policy as managed profile selection.
- Refresh behavior is now explicit: account and role discovery only runs when refresh is selected by the user.

### Fixed

- Reduced false SSO reauthentication prompts when switching between accounts and returning to a previously used account.
- Fixed profile/session matching issues caused by case divergence in session naming (for example, Matera-session vs matera-session).
- Improved consistency of profile ordering and selection persistence across awsswitch, eksswitch, and last shortcuts.
