"""Remove the legacy plaintext User-scope environment-variable secrets.

    uv run python scripts/remove_legacy_env_secrets.py

Step 2 of the owner's migration (amendment 5): only run this *after* the
Credential-Manager-only reboot recovery proof has passed. Running it
before that would leave no working copy anywhere if the Credential Manager
migration or the rewritten supervisor had a bug the proof would otherwise
have caught first.

Deletes CRUMBLR_DATABASE_URL/CRUMBLR_MT5_LOGIN/CRUMBLR_MT5_PASSWORD/
CRUMBLR_MT5_SERVER from `HKEY_CURRENT_USER\\Environment` (User scope) only
-- never touches Credential Manager, never touches Machine-scope variables,
never prints a value. Confirms each one is actually gone from the registry
afterwards rather than assuming the delete call worked.
"""

from __future__ import annotations

import sys
import winreg

LEGACY_NAMES = (
    "CRUMBLR_DATABASE_URL",
    "CRUMBLR_MT5_LOGIN",
    "CRUMBLR_MT5_PASSWORD",
    "CRUMBLR_MT5_SERVER",
)


def main() -> int:
    if sys.platform != "win32":
        print("error: this only makes sense on Windows", file=sys.stderr)
        return 2

    removed = []
    already_absent = []
    for name in LEGACY_NAMES:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS
        ) as key:
            try:
                winreg.DeleteValue(key, name)
                removed.append(name)
            except FileNotFoundError:
                already_absent.append(name)

    for name in removed:
        print(f"  {name}: removed from User-scope environment")
    for name in already_absent:
        print(f"  {name}: already absent")

    print()
    print("Reminder: this process (and any already-running process) still has the")
    print("old value in its own inherited environment until it restarts. A new")
    print("process (including the next Scheduled Task run) will no longer see it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
