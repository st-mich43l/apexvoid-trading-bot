"""Instrument vs account runtime option-scope audit."""

from __future__ import annotations

from typing import Literal

from app.configuration.ctrader_option_classification import (
  AUTO_TRADE_OPTIONS_CLASSIFICATION,
  FEED_OPTIONS_CLASSIFICATION,
)


Scope = Literal[
  "account",
  "instrument",
  "derived",
  "secret",
  "bootstrap",
  "deprecated_compatibility",
]

# Every Feed/AutoTrade property classified into runtime scope for PR3 audits.
# Unclassified must remain zero.

FEED_RUNTIME_SCOPE: dict[str, Scope] = {
  "AccessToken": "secret",
  "AccountId": "account",
  "BackfillBars": "instrument",
  "BarQualityLookback": "instrument",
  "BarsChannel": "account",
  "BarsWindowMax": "instrument",
  "CTraderSymbol": "instrument",
  "ClientId": "account",
  "ClientSecret": "secret",
  "ExpectedBroker": "account",
  "HeartbeatFile": "bootstrap",
  "Host": "account",
  "Port": "account",
  "RedisSymbol": "derived",
  "RedisUrl": "account",
  "RefreshToken": "secret",
  "RefreshTokenFile": "bootstrap",
  "RefreshTokenKey": "bootstrap",
  "RequestTimeout": "account",
  "Timeframes": "instrument",
  "TokenCheckInterval": "account",
  "TokenRefreshLead": "account",
}

# Auto-trade: trading knobs that become instrument-scoped under multi-symbol;
# shared streams / account demo guards stay account-scoped.
_ACCOUNT_AUTO_TRADE = {
  "CandidateStream",
  "EventStream",
  "ExpectedBroker",
  "RequireDemoAccount",
  "RequireDemoOnlyToken",
  "Label",
  "Profile",
  "PollMilliseconds",
  "Enabled",
  "DryRun",
  "ContractMode",
  "CandidateContractVersion",
  "ConfigManifestVersion",
  "EquityTableVersion",
}

_DEPRECATED_AUTO_TRADE = {
  "Symbols",  # compatibility projection; prefer live_instruments / registry
}

_DERIVED_AUTO_TRADE = {
  # reserved if any appear later
}


def auto_trade_runtime_scope(property_name: str) -> Scope:
  if property_name in _ACCOUNT_AUTO_TRADE:
    return "account"
  if property_name in _DEPRECATED_AUTO_TRADE:
    return "deprecated_compatibility"
  if property_name in _DERIVED_AUTO_TRADE:
    return "derived"
  # Remaining AutoTrade behaviour-affecting fields are instrument-scoped.
  classification = AUTO_TRADE_OPTIONS_CLASSIFICATION[property_name][0]
  if classification == "secret_environment":
    return "secret"
  if classification == "bootstrap_environment":
    return "bootstrap"
  if classification == "derived_runtime":
    return "derived"
  if classification == "deprecated_compatibility":
    return "deprecated_compatibility"
  return "instrument"


def build_instrument_runtime_scope_audit() -> dict[str, object]:
  feed_scopes = dict(FEED_RUNTIME_SCOPE)
  auto_scopes = {
    prop: auto_trade_runtime_scope(prop)
    for prop in AUTO_TRADE_OPTIONS_CLASSIFICATION
  }
  missing_feed = sorted(set(FEED_OPTIONS_CLASSIFICATION) - set(feed_scopes))
  missing_auto = sorted(
    prop for prop, scope in auto_scopes.items() if scope is None
  )
  counts: dict[str, int] = {
    "account": 0,
    "instrument": 0,
    "bootstrap": 0,
    "secret": 0,
    "derived": 0,
    "deprecated_compatibility": 0,
    "unclassified": len(missing_feed) + len(missing_auto),
  }
  for scope in feed_scopes.values():
    counts[scope] += 1
  for scope in auto_scopes.values():
    counts[scope] += 1
  return {
    "manifest_version": 2,
    "counts": counts,
    "feed": [
      {"property": prop, "scope": scope}
      for prop, scope in sorted(feed_scopes.items())
    ],
    "auto_trade": [
      {"property": prop, "scope": scope}
      for prop, scope in sorted(auto_scopes.items())
    ],
    "unclassified_properties": missing_feed + missing_auto,
  }


def assert_scope_audit_complete() -> dict[str, object]:
  audit = build_instrument_runtime_scope_audit()
  counts = audit["counts"]
  assert isinstance(counts, dict)
  if counts.get("unclassified", 0) != 0:
    raise AssertionError(
      f"unclassified runtime fields remain: {audit['unclassified_properties']}"
    )
  return audit
