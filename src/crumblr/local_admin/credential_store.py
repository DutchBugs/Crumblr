"""Windows Credential Manager (Generic credentials) as the secret-at-rest store.

Every secret this platform's local runtime needs (MT5 login, the database
URL, the Gateway credential, the local Agent bearer token) is written here
through `CredWriteW` and read back through `CredReadW` -- the same Win32
API the Windows Credential Manager Control Panel applet itself uses.
Generic credentials are encrypted at rest with DPAPI, scoped to the
Windows user account that wrote them, which is exactly the boundary the
owner asked for: the same account that runs the Scheduled Task can read
them back, nothing else can.

Nothing here ever writes a secret to a file, environment variable,
config/*.yaml, `crumblr_soak`, a journal, a CLI argument, or a log line.
The one exception a caller must uphold itself: once `read()` returns a
plaintext value, what that caller does with it is that caller's
responsibility -- `host_supervisor.ps1`'s own read path assigns it straight
into the one child process's environment and nothing else. `status()`
exists specifically so a caller (the Local Host Admin API) can answer
"configured or missing, and since when" without ever decoding the blob
into a string at all.

Deferred-import pattern, matching `crumblr.mt5_gateway.client.load_mt5_module`:
importing this module is safe on any platform (so `ruff`/`mypy` and the
rest of the test suite run everywhere); only *calling* one of its
functions off Windows raises a clear, immediate error.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import ctypes

CRED_TARGET_PREFIX = "Crumblr:"
"""Every entry this module writes/reads is namespaced under this prefix, so
`cmdkey /list` or the Credential Manager Control Panel applet immediately
shows which entries belong to this platform."""

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


class WindowsOnlyError(RuntimeError):
    """Raised when a Credential Manager call is attempted off Windows."""


@dataclass(frozen=True)
class CredentialStatus:
    """Whether a secret is configured, and when it was last written.

    Deliberately carries no value-shaped field -- `status()` never decodes
    the credential blob, so there is nothing here that could be
    accidentally logged or rendered.
    """

    configured: bool
    last_written_utc: datetime | None


def _require_windows() -> None:
    if sys.platform != "win32":
        raise WindowsOnlyError(
            "Windows Credential Manager is only available on Windows; "
            f"this process is running on {sys.platform!r}"
        )


def _target_name(name: str) -> str:
    return f"{CRED_TARGET_PREFIX}{name}"


def _advapi32() -> ctypes.WinDLL:
    _require_windows()
    import ctypes

    return ctypes.WinDLL("Advapi32.dll", use_last_error=True)


def _credential_struct() -> type[ctypes.Structure]:
    import ctypes
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = (
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        )

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = (
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", _FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        )

    return _CREDENTIAL


def _filetime_to_datetime(low: int, high: int) -> datetime | None:
    value = (high << 32) | low
    if value == 0:
        return None
    # FILETIME: 100ns intervals since 1601-01-01 UTC.
    epoch_offset_seconds = 11644473600
    seconds = value / 10_000_000 - epoch_offset_seconds
    return datetime.fromtimestamp(seconds, tz=UTC)


def write(name: str, value: str) -> None:
    """Write (or replace) one secret. Only ever called on explicit owner action."""
    import ctypes
    from ctypes import wintypes

    advapi32 = _advapi32()
    credential_type = _credential_struct()

    blob = value.encode("utf-16-le")
    blob_buf = ctypes.create_string_buffer(blob, len(blob))
    target_name = _target_name(name)

    cred = credential_type()
    cred.Flags = 0
    cred.Type = _CRED_TYPE_GENERIC
    cred.TargetName = target_name
    cred.Comment = None
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(blob_buf, ctypes.POINTER(ctypes.c_byte))
    cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = None

    advapi32.CredWriteW.argtypes = [ctypes.POINTER(credential_type), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    if not advapi32.CredWriteW(ctypes.byref(cred), 0):
        error = ctypes.get_last_error()
        raise OSError(f"CredWriteW failed for {name!r}: WinError {error}")


def read(name: str) -> str | None:
    """The plaintext value, or `None` if this secret has never been set.

    Callers must not print, log, or otherwise persist what this returns
    anywhere but the one child-process environment it is about to seed.
    """
    import ctypes
    from ctypes import wintypes

    advapi32 = _advapi32()
    credential_type = _credential_struct()

    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(credential_type)),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]

    cred_ptr = ctypes.POINTER(credential_type)()
    ok = advapi32.CredReadW(_target_name(name), _CRED_TYPE_GENERIC, 0, ctypes.byref(cred_ptr))
    if not ok:
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return None
        raise OSError(f"CredReadW failed for {name!r}: WinError {error}")
    try:
        cred = cred_ptr.contents
        size = cred.CredentialBlobSize
        if size == 0:
            return ""
        raw = ctypes.string_at(cred.CredentialBlob, size)
        return raw.decode("utf-16-le")
    finally:
        advapi32.CredFree(cred_ptr)


def status(name: str) -> CredentialStatus:
    """Configured/missing plus last-written time -- never the value itself."""
    import ctypes
    from ctypes import wintypes

    advapi32 = _advapi32()
    credential_type = _credential_struct()

    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(credential_type)),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]

    cred_ptr = ctypes.POINTER(credential_type)()
    ok = advapi32.CredReadW(_target_name(name), _CRED_TYPE_GENERIC, 0, ctypes.byref(cred_ptr))
    if not ok:
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return CredentialStatus(configured=False, last_written_utc=None)
        raise OSError(f"CredReadW failed for {name!r}: WinError {error}")
    try:
        cred = cred_ptr.contents
        last_written = _filetime_to_datetime(
            cred.LastWritten.dwLowDateTime, cred.LastWritten.dwHighDateTime
        )
        return CredentialStatus(
            configured=cred.CredentialBlobSize > 0, last_written_utc=last_written
        )
    finally:
        advapi32.CredFree(cred_ptr)


def delete(name: str) -> bool:
    """Delete one secret. Only ever called on explicit owner action.

    Returns `False` if it was already absent (not an error -- deleting
    something already gone is not a failure), `True` if a real entry was
    removed.
    """
    import ctypes
    from ctypes import wintypes

    advapi32 = _advapi32()
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL

    ok = advapi32.CredDeleteW(_target_name(name), _CRED_TYPE_GENERIC, 0)
    if not ok:
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return False
        raise OSError(f"CredDeleteW failed for {name!r}: WinError {error}")
    return True
