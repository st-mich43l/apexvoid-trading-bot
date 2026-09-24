from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.scripts.drop_manual_algo_charts import _archive

pytestmark = pytest.mark.no_database


class _FakeDatabase:
  async def fetch(self, query: str):
    if "information_schema.columns" in query:
      return [
        {"column_name": "id", "data_type": "bigint", "is_nullable": "NO"},
        {"column_name": "price", "data_type": "numeric", "is_nullable": "NO"},
      ]
    return [
      {"id": 1, "price": Decimal("4051.10"), "captured_at": datetime(2026, 9, 24, tzinfo=timezone.utc)},
    ]


def test_archive_is_complete_restricted_and_checksummable(tmp_path):
  path = tmp_path / "manual_algo_charts.json"
  digest = asyncio.run(_archive(_FakeDatabase(), path, 1))

  encoded = path.read_bytes()
  document = json.loads(encoded)
  assert document["format"] == "apexvoid.manual_algo_charts.archive.v1"
  assert document["row_count"] == 1
  assert document["rows"][0]["price"] == "4051.10"
  assert hashlib.sha256(encoded).hexdigest() == digest
  assert path.stat().st_mode & 0o777 == 0o600


def test_archive_refuses_a_changed_row_count(tmp_path):
  with pytest.raises(RuntimeError, match="archive count changed"):
    asyncio.run(_archive(_FakeDatabase(), tmp_path / "bad.json", 2))
