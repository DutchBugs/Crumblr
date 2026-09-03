"""Versioned, fail-closed configuration (build.md §17).

Two rules shape this module:

1. **No permissive defaults for anything risk-bearing.** Every limit is a
   required field. A config file that forgets `max_daily_loss` fails to load;
   it does not quietly trade without one.
2. **Config is versioned and immutable per decision.** `config_version` is a
   content hash, so a decision capsule can name the exact configuration that
   produced it.

Secrets never live here. build.md §21 keeps MT5 credentials out of the
repository, so the loader actively rejects credential-shaped keys rather than
trusting reviewers to catch them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from crumblr.domain.enums import Environment
from crumblr.domain.hashing import fingerprint
from crumblr.domain.models import Symbol, VersionTag
from crumblr.domain.money import RiskFraction
from crumblr.observability.logging import get_logger

_log = get_logger("config")

LIVE_OVERRIDE_ENV_VAR = "CRUMBLR_ALLOW_LIVE"
"""Loading a live configuration additionally requires this to be set to "1"."""

_SECRET_KEY_MARKERS = ("password", "secret", "token", "api_key", "apikey", "credential")

DEMO_ONLY_ENVIRONMENTS: frozenset[Environment] = frozenset({Environment.PAPER, Environment.SHADOW})


class ConfigSection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class MarketConfig(ConfigSection):
    canonical_symbol: Symbol
    enabled: bool

    expected_spec_version: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    """The approved, pinned `InstrumentSpec.spec_version` for this symbol —

    review 1.19 §4 (F-055). `None` (the default) means no baseline has been
    explicitly authorized yet, and reconciliation must read `UNKNOWN` for
    this symbol's instrument spec rather than silently trusting whichever
    observation happened to arrive first (`instrument_specs.earliest()` is
    trust-on-first-use, not authority — a database reset or a first real
    session against a materially different broker configuration would
    otherwise "pin" itself).

    Set this only after a real observation (F-051) has been reviewed and
    accepted as correct — the value itself is still discovered from the
    terminal, never invented; this field records that a human looked at
    what was discovered and approved it. Changing it later is exactly as
    reviewable as any other config edit, which is the point: an approved
    baseline changes only through an explicit, git-visible act, not because
    a database happened to be recreated."""


class RiskConfig(ConfigSection):
    """Every field is required. build.md §17: do not ship production defaults."""

    max_risk_per_trade: RiskFraction
    max_open_risk: RiskFraction
    max_daily_loss: RiskFraction
    max_drawdown: RiskFraction
    max_orders_per_hour: int = Field(ge=0)
    max_open_positions: int = Field(ge=0)
    """An operational circuit-breaker ceiling, not a trading rule and not

    the owner's portfolio budget — that is `max_open_risk`, enforced
    against measured open risk (`risk/portfolio_risk.py::assess_open_risk`,
    owner risk policy v1, D1.4). Position count alone must never be a
    refusal reason on its own account; this exists only so a runaway
    strategy cannot open unbounded tickets before the risk budget itself
    would catch it. See `review/DEVIATIONS.md` D-053."""
    min_stop_distance_points: int = Field(ge=0)

    approved_config_version: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    """The risk policy version an owner has explicitly approved for real

    submission — F-049 (`review/FEEDBACK.md`), one of `SubmissionGate`'s
    nine required conditions. Same pattern as
    `MarketConfig.expected_spec_version` (F-055): `None` (the default, and
    every shipped config today) means unapproved, and `SubmissionGate`
    reads that as closed, never as "assume yes." Checked against
    `PlatformConfig.config_version` — set this only after the owner has
    reviewed the actual numbers this `RiskConfig` carries (`max_risk_per_trade`,
    `max_daily_loss`, `max_drawdown`, the intraday deadlines, HALT-reset
    authority — build.md §29 Q7/Q8/Q12), not merely after config validates."""

    @model_validator(mode="after")
    def _check_budget_ordering(self) -> Self:
        if self.max_risk_per_trade > self.max_open_risk:
            raise ValueError(
                f"max_risk_per_trade {self.max_risk_per_trade} exceeds "
                f"max_open_risk {self.max_open_risk}: one trade cannot outweigh the portfolio"
            )
        if self.max_daily_loss > self.max_drawdown:
            raise ValueError(
                f"max_daily_loss {self.max_daily_loss} exceeds "
                f"max_drawdown {self.max_drawdown}: the daily gate would never trip first"
            )
        return self


class ExecutionConfig(ConfigSection):
    max_spread_points: int = Field(gt=0)
    max_market_data_age_ms: int = Field(gt=0)
    order_timeout_ms: int = Field(gt=0)
    max_slippage_points: int = Field(ge=0)

    submission_enabled: bool = False
    """Whether the execution adapter is explicitly enabled for real

    `order_send` — F-049's own required condition, never inferred from any
    other setting. Defaults to `False`, and no shipped config file sets it
    to `True`; flipping it is a deliberate, git-reviewed, owner-made
    change, not something that follows automatically from any other flag
    in this file being set."""

    feedback_2_0_approved: bool = False
    """Whether `feedback.2.0.md` has given its explicit GO — F-049's final

    required condition. Defaults to `False`. This field records that the
    formal pre-submission review happened and passed; it is not itself
    that review, and setting it to `True` without `feedback.2.0.md`
    actually having done so defeats the entire point of the gate."""

    flatten_submission_enabled: bool = False
    """Whether the automatic intraday flatten is explicitly enabled to

    submit — `risk/flatten_gate.py`'s own required condition (core
    critical path item 7, ADR-009 §2), never inferred from
    `submission_enabled`. Deliberately a fourth, separate flag: ADR-004
    §5.1 requires the automatic flatten to stay distinct from ordinary
    order submission, and build.md §8.2's decoupling rule means "I
    enabled order submission" must not silently also mean "I enabled
    automatic liquidation." Defaults to `False`; no shipped config sets
    it to `True`."""

    approved_canary_account_ref: str | None = None
    """The owner-approved exact DEMO account reference for real

    submission (Phase B item B7, `review/adr/ADR-017-account-reference
    -pin.md`). A `login_hash`-style fingerprint
    (`fingerprint({"login": ..., "server": ...})[:16]`, matching
    `AccountState.login_hash`/`ExpectedState.expected_account_ref`'s own
    technique) — never the raw account number, never a credential.
    Defaults `None`/unset; no shipped config sets it. Setting it is a
    deliberate, git-reviewed, owner-made act (Phase E), the same
    "record that a specific real-world identity was approved" role
    `approved_config_version` plays for the risk config."""


class TradingAgentConfig(ConfigSection):
    strategy_id: VersionTag
    strategy_version: VersionTag
    model_version: VersionTag | None = None


class SupervisorConfig(ConfigSection):
    enabled: bool
    veto_on_unknown_regime: bool
    halt_on_reconciliation_mismatch: bool
    policy_version: VersionTag

    max_intents_per_hour: int | None = Field(ge=1)
    """Signal-frequency anomaly threshold, or `null` for "not calibrated".

    Required, and required to be stated rather than defaulted, because the two
    honest states are different claims and the difference matters. A number is
    a claim that this rate is anomalous. `null` is a claim that nobody knows
    yet — which is the truth until real EUR/USD observations exist, and which
    the supervisor then reports as an uncalibrated check rather than as a
    check that passed (review 1.6 F-024).

    The lower bound is 1 rather than 0: a threshold of zero would veto every
    intent, which is a halt written as a rate limit."""


class IntradayConfig(ConfigSection):
    """Owner decision O-003 — v1 holds nothing overnight.

    The session boundary itself is not here. It is 17:00 New York, a market
    fact already encoded in `trading_agent.sessions`, and a second copy of it
    in YAML would be a second definition that could drift. What lives here is
    how far in front of that boundary the two deadlines sit, because those are
    risk policy and belong to a human.

    Both are required. An intraday policy with a missing deadline is a policy
    that holds overnight, which is the outcome O-003 forbids.
    """

    enabled: bool
    last_entry_minutes_before_close: int = Field(ge=0)
    flatten_minutes_before_close: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_deadline_ordering(self) -> Self:
        if self.last_entry_minutes_before_close < self.flatten_minutes_before_close:
            raise ValueError(
                f"last_entry_minutes_before_close {self.last_entry_minutes_before_close} is "
                f"closer to the close than flatten_minutes_before_close "
                f"{self.flatten_minutes_before_close}: entries would still be accepted after "
                "the book was required to be flat"
            )
        return self


class AccountGuardConfig(ConfigSection):
    """build.md §8.1 — the guard that keeps paper mode on a demo account.

    Everything here is a fact about the account that must be *verified against
    the terminal*, never assumed. Review 1.5 is explicit that broker metadata
    is discovered from the real account rather than hard-coded from an
    example, so each field is a claim the guard checks rather than a value the
    platform relies on being true.

    `expected_login` stays nullable and carries no credential: an account
    number is an identifier, and the password that goes with it lives in the
    secret store (build.md §21) where this loader would refuse it anyway.
    """

    expected_server: Annotated[str, Field(min_length=1, max_length=128)]
    expected_login: int | None = None
    require_demo_account: bool

    expected_currency: Annotated[str, Field(min_length=3, max_length=8)] | None = None
    """Account currency. A mismatch means the risk budget is being measured in
    a unit nobody agreed to — 0.5% of an account is a different amount of money
    in EUR than in USD."""

    expected_leverage: int | None = Field(default=None, gt=0)
    """Leverage the account is expected to offer. It sets margin per lot, so a
    silent change moves how much of the account a position ties up without
    changing anything the strategy or the risk engine can see."""


class PlatformConfig(ConfigSection):
    environment: Environment
    markets: tuple[MarketConfig, ...]
    risk: RiskConfig
    execution: ExecutionConfig
    trading_agent: TradingAgentConfig
    supervisor: SupervisorConfig
    account_guard: AccountGuardConfig
    intraday: IntradayConfig
    live_trading_acknowledged: bool = False

    @model_validator(mode="after")
    def _check_markets(self) -> Self:
        if not self.markets:
            raise ValueError("at least one market must be configured")
        symbols = [market.canonical_symbol for market in self.markets]
        if len(symbols) != len(set(symbols)):
            raise ValueError("duplicate canonical_symbol in markets")
        return self

    @model_validator(mode="after")
    def _check_environment_guardrails(self) -> Self:
        """Paper and shadow may only ever point at a demo account."""
        if (
            self.environment in DEMO_ONLY_ENVIRONMENTS
            and not self.account_guard.require_demo_account
        ):
            raise ValueError(
                f"environment {self.environment} requires account_guard.require_demo_account: true"
            )
        if self.environment is Environment.LIVE and not self.live_trading_acknowledged:
            raise ValueError(
                "a live configuration must set live_trading_acknowledged: true "
                "and be promoted by a recorded human decision"
            )
        return self

    @property
    def config_version(self) -> str:
        """Content hash of the configuration's substantive content, for the

        decision capsule and `SubmissionGate` condition 6
        (`RiskConfig.approved_config_version`, F-049/ADR-006).

        Excludes the five governance/approval fields
        (`risk.approved_config_version`, `execution.submission_enabled`,
        `execution.feedback_2_0_approved`, `execution.flatten_submission_enabled`,
        `execution.approved_canary_account_ref`) deliberately: this hash is
        what an owner reviews and approves ("the actual numbers this
        `RiskConfig` carries" — `RiskConfig.approved_config_version`'s own
        docstring), and an approval field is not itself one of those
        numbers. Hashing it anyway would make it self-referential — writing
        the approved hash into the file changes the file, which changes the
        hash the write was supposed to match, which can never converge
        (found while adding the wiring in `application/execution.py` that
        actually evaluates this condition; empirically confirmed
        unsatisfiable before this fix, see F-062, `review/FEEDBACK.md`).
        `flatten_submission_enabled` (core critical path item 7) and
        `approved_canary_account_ref` (Phase B item B7,
        `review/adr/ADR-017-account-reference-pin.md`) each joined the
        exclusion set for the identical reason the day they were added,
        rather than repeating F-062's mistake a second/third time. Any
        other field change still produces a new version, unchanged from
        before this fix (`tests/unit/test_config.py::TestConfigVersioning`)."""
        payload = self.model_dump(
            mode="json",
            exclude={
                "risk": {"approved_config_version"},
                "execution": {
                    "submission_enabled",
                    "feedback_2_0_approved",
                    "flatten_submission_enabled",
                    "approved_canary_account_ref",
                },
            },
        )
        return fingerprint(payload)

    def enabled_symbols(self) -> tuple[str, ...]:
        return tuple(market.canonical_symbol for market in self.markets if market.enabled)

    def market_for(self, canonical_symbol: str) -> MarketConfig | None:
        return next(
            (market for market in self.markets if market.canonical_symbol == canonical_symbol),
            None,
        )


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge `overlay` onto `base`. Mappings merge; every other value replaces."""
    merged = dict(base)
    for key, overlay_value in overlay.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(overlay_value, dict):
            merged[key] = _deep_merge(base_value, overlay_value)
        else:
            merged[key] = overlay_value
    return merged


def _assert_no_secrets(data: Any, path: str = "") -> None:
    """Refuse configuration that looks like it carries a credential."""
    if isinstance(data, dict):
        for key, value in data.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in _SECRET_KEY_MARKERS):
                location = f"{path}.{key}" if path else str(key)
                raise ValueError(
                    f"configuration key {location!r} looks like a credential; "
                    "secrets belong in the secret store, never in config files"
                )
            _assert_no_secrets(value, f"{path}.{key}" if path else str(key))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            _assert_no_secrets(item, f"{path}[{index}]")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"configuration file not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return loaded


def load_config(
    environment: Environment,
    *,
    config_dir: Path,
    allow_live: bool | None = None,
) -> PlatformConfig:
    """Load `base.yaml` overlaid with `<environment>.yaml`.

    Loading a live configuration additionally requires the `CRUMBLR_ALLOW_LIVE`
    environment variable, so no code path reaches a live account by editing a
    YAML file alone.
    """
    if environment is Environment.LIVE:
        permitted = (
            allow_live if allow_live is not None else os.getenv(LIVE_OVERRIDE_ENV_VAR) == "1"
        )
        if not permitted:
            raise PermissionError(
                f"refusing to load a live configuration without {LIVE_OVERRIDE_ENV_VAR}=1"
            )

    base = _read_yaml(config_dir / "base.yaml")
    overlay = _read_yaml(config_dir / f"{environment.value}.yaml")
    merged = _deep_merge(base, overlay)
    _assert_no_secrets(merged)
    merged["environment"] = environment.value
    config = PlatformConfig.model_validate(merged)
    _log.info(
        "config.loaded",
        environment=config.environment.value,
        config_version=config.config_version,
        symbols=list(config.enabled_symbols()),
        strategy_id=config.trading_agent.strategy_id,
        require_demo_account=config.account_guard.require_demo_account,
    )
    return config
