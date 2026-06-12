# Changelog

All notable changes for this project are documented in this file.

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
