"""Reproducible local verification plan; no CI or runtime integration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Callable


@dataclass(frozen=True, slots=True)
class VerificationCommand:
  surface: str
  command: tuple[str, ...]
  working_directory: str


@dataclass(frozen=True, slots=True)
class ActivationVerificationPlan:
  python: tuple[VerificationCommand, ...]
  csharp: tuple[VerificationCommand, ...]


@dataclass(frozen=True, slots=True)
class CSharpVerificationResult:
  status: str
  sdk_available: bool
  info_exit_code: int | None
  test_exit_codes: tuple[int, ...]
  verified: bool


def detect_csharp_verification_status(
  executable_lookup: Callable[[str], str | None] = shutil.which,
) -> CSharpVerificationResult:
  available = executable_lookup("dotnet") is not None
  return CSharpVerificationResult(
    status="available_not_executed" if available else "not_verified_locally",
    sdk_available=available,
    info_exit_code=None,
    test_exit_codes=(),
    verified=False,
  )


def activation_verification_plan() -> ActivationVerificationPlan:
  return ActivationVerificationPlan(
    python=(
      VerificationCommand(
        surface="catalog",
        command=(
          "python", "-m", "app.configuration.catalog_validation",
          "../docs/configuration/config-catalog-phase-2a-normalized.json",
        ),
        working_directory="algo-bot",
      ),
      VerificationCommand(
        surface="generated_contracts",
        command=("python", "-m", "app.configuration.generate", "--check"),
        working_directory="algo-bot",
      ),
      VerificationCommand(
        surface="phase_2d1_tests",
        command=(
          "pytest", "-q", "tests/test_config_legacy_usage_audit.py",
          "tests/test_config_legacy_access_generation.py",
          "tests/test_config_facade.py", "tests/test_config_authority.py",
          "tests/test_config_activation_readiness.py",
        ),
        working_directory="algo-bot",
      ),
    ),
    csharp=(
      VerificationCommand(
        surface="dotnet_sdk",
        command=("dotnet", "--info"),
        working_directory=".",
      ),
      VerificationCommand(
        surface="ctrader_tests",
        command=(
          "dotnet", "test", "tests/CTraderFeed.Tests.csproj",
          "--configuration", "Release",
        ),
        working_directory="ctrader-engine",
      ),
    ),
  )


def verify_csharp_locally(
  repository_root: Path,
  *,
  executable_lookup: Callable[[str], str | None] = shutil.which,
  runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> CSharpVerificationResult:
  """Run the declared local C# checks only when an SDK is installed."""
  if executable_lookup("dotnet") is None:
    return detect_csharp_verification_status(executable_lookup)
  plan = activation_verification_plan()
  results = []
  for item in plan.csharp:
    result = runner(
      item.command,
      cwd=repository_root / item.working_directory,
      check=False,
      capture_output=True,
      text=True,
    )
    results.append(result.returncode)
  verified = all(code == 0 for code in results)
  return CSharpVerificationResult(
    status="verified" if verified else "failed",
    sdk_available=True,
    info_exit_code=results[0],
    test_exit_codes=tuple(results[1:]),
    verified=verified,
  )
