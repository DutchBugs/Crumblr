"""One-time migration: copy the 4 already-known plaintext User-scope

environment-variable secrets into Windows Credential Manager.

    uv run python scripts/migrate_secrets_to_credential_manager.py

Owner's Local Host Admin work order (2026-09-09), amendment 5: "copy to
Credential Manager first; do not delete plaintext User-scope copies yet;
make the new supervisor read Credential Manager only; prove local
connectivity and controlled reboot recovery using that path; after PASS,
remove the legacy User-scope copies."

This script performs only the first step. It reads each of
CRUMBLR_DATABASE_URL/CRUMBLR_MT5_LOGIN/CRUMBLR_MT5_PASSWORD/CRUMBLR_MT5_SERVER
directly from the `HKEY_CURRENT_USER\\Environment` registry key -- the same
place `[System.Environment]::GetEnvironmentVariable($name, "User")` (what
the earlier, now-superseded plan used) reads from -- and writes each one
into Credential Manager under the same name. It never deletes the registry
copy (that is a separate, later, explicitly owner-authorised step --
`scripts/remove_legacy_env_secrets.py`, run only after the Credential-
Manager-only reboot proof has passed). It never prints a secret value,
only whether each name was found and migrated.

`CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL` and `LOCAL_AGENT_SERVICE_TOKEN` are
deliberately not handled here: they were never persisted as plaintext
User-scope variables in the first place (owner's own finding, 2026-09-09),
so there is nothing to migrate for them -- they must be entered once,
directly into Credential Manager, by the owner (via `scripts/local_host_admin.py`).
"""

from __future__ import annotations

import sys
import winreg

from crumblr.local_admin import credential_store

MIGRATABLE_NAMES = (
    "CRUMBLR_DATABASE_URL",
    "CRUMBLR_MT5_LOGIN",
    "CRUMBLR_MT5_PASSWORD",
    "CRUMBLR_MT5_SERVER",
)


def _read_user_env_registry_value(name: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return None
    return str(value) if value else None


def main() -> int:
    if sys.platform != "win32":
        print("error: this migration only makes sense on Windows", file=sys.stderr)
        return 2

    migrated = []
    missing = []
    for name in MIGRATABLE_NAMES:
        value = _read_user_env_registry_value(name)
        if not value:
            missing.append(name)
            continue
        credential_store.write(name, value)
        migrated.append(name)

    print(f"migrated {len(migrated)} of {len(MIGRATABLE_NAMES)} secrets into Credential Manager:")
    for name in migrated:
        print(f"  {name}: copied (registry copy left in place)")
    for name in missing:
        print(f"  {name}: no User-scope registry value found -- nothing to migrate")

    print()
    print("The registry User-scope copies were NOT deleted. Run")
    print("scripts/remove_legacy_env_secrets.py yourself only after the Credential-")
    print("Manager-only reboot proof has passed.")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
