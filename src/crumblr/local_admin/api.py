"""The Local Host Admin FastAPI app: a localhost-only administration surface.

Deliberately its own app object, its own process, its own port -- never
mounted into or imported by `crumblr.dashboard`, so Dashboard v0's
"only GET routes exist" guarantee stays true by construction, not by
convention. Nothing in this module (or anywhere else in `local_admin`)
imports `crumblr.risk`, `crumblr.persistence.safety_state`, or
`crumblr.agent_gateway` -- there is no code path here that could reach a
HALT reset, Risk/Policy authority, or a `TradingAssignment` mutation. See
`tests/unit/test_local_admin_boundary.py` for the structural proof.

Only six secrets and ten settings keys exist for this app to touch at all
(`registry.py`) -- both are allowlists a write is checked against, not
blocklists trying to exclude what must stay unreachable.

Every mutating route, and every route at all, requires the per-process
admin token printed to the console at startup (`scripts/local_host_admin.py`).
That token is not one of the six stored secrets: it is regenerated every
run, never written to disk, and exists only to stop another local
process/browser tab from driving this app -- the real boundary is the
process/port being loopback-only in the first place.
"""

from __future__ import annotations

import secrets as _secrets
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from crumblr.local_admin import credential_store, settings_store
from crumblr.local_admin.registry import ALLOWED_SECRET_NAMES, ALLOWED_SETTINGS_KEYS
from crumblr.mt5_gateway.client import Mt5Client, Mt5Credentials, Mt5UnavailableError
from crumblr.persistence.engine import create_db_engine

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


class SecretStatusOut(BaseModel):
    name: str
    configured: bool
    last_written_utc: datetime | None


class SecretWriteIn(BaseModel):
    value: str


class TestResult(BaseModel):
    ok: bool
    detail: str


def create_app(
    *, admin_token: str, settings_path: Path = settings_store.DEFAULT_SETTINGS_PATH
) -> FastAPI:
    app = FastAPI(title="Crumblr Local Host Admin")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    def require_token(x_local_admin_token: Annotated[str | None, Header()] = None) -> None:
        if not x_local_admin_token or not _secrets.compare_digest(x_local_admin_token, admin_token):
            raise HTTPException(status_code=401, detail="missing or wrong X-Local-Admin-Token")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Any) -> Any:
        return templates.TemplateResponse(
            request, "admin.html", {"secret_names": ALLOWED_SECRET_NAMES}
        )

    @app.get("/api/secrets", dependencies=[Depends(require_token)])
    def list_secrets() -> list[SecretStatusOut]:
        results = []
        for name in ALLOWED_SECRET_NAMES:
            st = credential_store.status(name)
            results.append(
                SecretStatusOut(
                    name=name, configured=st.configured, last_written_utc=st.last_written_utc
                )
            )
        return results

    @app.post("/api/secrets/{name}", dependencies=[Depends(require_token)])
    def write_secret(name: str, body: SecretWriteIn) -> SecretStatusOut:
        if name not in ALLOWED_SECRET_NAMES:
            raise HTTPException(status_code=404, detail=f"not a recognised secret: {name!r}")
        if not body.value:
            raise HTTPException(status_code=400, detail="value must not be empty")
        credential_store.write(name, body.value)
        st = credential_store.status(name)
        return SecretStatusOut(
            name=name, configured=st.configured, last_written_utc=st.last_written_utc
        )

    @app.delete("/api/secrets/{name}", dependencies=[Depends(require_token)])
    def delete_secret(name: str) -> SecretStatusOut:
        if name not in ALLOWED_SECRET_NAMES:
            raise HTTPException(status_code=404, detail=f"not a recognised secret: {name!r}")
        credential_store.delete(name)
        return SecretStatusOut(name=name, configured=False, last_written_utc=None)

    @app.get("/api/settings", dependencies=[Depends(require_token)])
    def get_settings() -> dict[str, Any]:
        return settings_store.read_settings(settings_path)

    @app.put("/api/settings", dependencies=[Depends(require_token)])
    def put_settings(updates: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(updates) - set(ALLOWED_SETTINGS_KEYS))
        if unknown:
            raise HTTPException(status_code=400, detail=f"not a recognised setting: {unknown}")
        return settings_store.write_settings(updates, settings_path)

    @app.post("/api/test/postgres", dependencies=[Depends(require_token)])
    def test_postgres() -> TestResult:
        url = credential_store.read("CRUMBLR_DATABASE_URL")
        if not url:
            return TestResult(ok=False, detail="CRUMBLR_DATABASE_URL is not configured")
        engine = create_db_engine(url)
        try:
            with engine.connect() as conn:
                conn.execute(text("select 1"))
        except SQLAlchemyError:
            return TestResult(ok=False, detail="connection failed -- see server logs for detail")
        finally:
            engine.dispose()
        return TestResult(ok=True, detail="connected")

    @app.post("/api/test/mt5", dependencies=[Depends(require_token)])
    def test_mt5() -> TestResult:
        login = credential_store.read("CRUMBLR_MT5_LOGIN")
        password = credential_store.read("CRUMBLR_MT5_PASSWORD")
        server = credential_store.read("CRUMBLR_MT5_SERVER")
        if not login or not password or not server:
            return TestResult(ok=False, detail="MT5 credentials are not fully configured")
        settings = settings_store.read_settings(settings_path)
        terminal_path = settings.get("mt5_terminal_path")
        client = Mt5Client()
        try:
            client.connect(
                Mt5Credentials(login=int(login), password=password, server=server),
                terminal_path=terminal_path,
            )
        except Mt5UnavailableError as error:
            return TestResult(ok=False, detail=f"MT5 module unavailable: {error}")
        except Exception as error:
            return TestResult(ok=False, detail=f"connect failed: {error}")
        else:
            return TestResult(ok=True, detail="connected and authorized (read-only)")
        finally:
            if client.is_connected:
                client.disconnect()

    @app.post("/api/test/static-agent", dependencies=[Depends(require_token)])
    def test_static_agent() -> TestResult:
        settings = settings_store.read_settings(settings_path)
        host = settings["static_agent_host"]
        port = settings["static_agent_port"]
        token_status = credential_store.status("LOCAL_AGENT_SERVICE_TOKEN")
        url = f"http://{host}:{port}/health"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                body = response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            return TestResult(ok=False, detail=f"unreachable at {url}: {error}")
        healthy = b'"status": "READY"' in body or b'"status":"READY"' in body
        token_note = (
            "LOCAL_AGENT_SERVICE_TOKEN configured locally"
            if token_status.configured
            else "LOCAL_AGENT_SERVICE_TOKEN NOT configured locally"
        )
        return TestResult(
            ok=healthy,
            detail=f"health={'READY' if healthy else 'not READY'}; {token_note}. "
            "(This checks reachability and local token presence, not that the "
            "Static Agent process itself was started with a matching token.)",
        )

    return app
