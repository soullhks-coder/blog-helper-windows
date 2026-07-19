# Blog Helper version policy

Blog Helper uses semantic versions in the `MAJOR.MINOR.PATCH` format.

- `MAJOR`: incompatible storage, automation, or packaging redesign (`2.0.0`)
- `MINOR`: a meaningful new user feature or menu (`1.1.0`)
- `PATCH`: a bug fix, UI adjustment, performance improvement, or updater change (`1.0.2`)

`1.0.1` therefore means major version 1, feature series 0, and the first maintenance update.

## Release rules

1. Update `version.json` and create the matching Git tag, such as `v1.0.2`.
2. GitHub Actions always publishes complete macOS and Windows installers for new installations and recovery.
3. It also creates a verified binary patch from the immediately previous version.
4. The app downloads the small patch first and validates both the source and completed-file SHA-256 hashes.
5. If a user skips versions or a local file differs, the updater automatically falls back to the complete installer.
6. Dependency or packaging-engine changes may require one complete update; ordinary releases after that use the small patch.
