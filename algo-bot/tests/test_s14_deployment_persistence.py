"""Stateful services must keep their state on a named volume, not in the container layer.

Production finding (2026-09-26): apache/kafka logs to /tmp by default, so the `kafkadata` volume
mounted at /var/lib/kafka/data stayed empty and every container recreate wiped topics and consumer
offsets; the Go engine's durable publication ledger had no volume at all. Both make the S14
lifecycle guarantees (committed offsets, terminal events, ledger ordering) false across a deploy.
"""

import re
from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deployment-template" / "docker-compose.yml.j2"


def _compose() -> dict:
  text = TEMPLATE.read_text()
  text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
  text = re.sub(r"\{\{.*?\}\}", "X", text)
  return yaml.safe_load(text)


def _named_mounts(service: dict) -> dict[str, str]:
  """destination -> volume name, for named-volume mounts only (bind mounts start with . or /)."""
  out = {}
  for entry in service.get("volumes", []):
    source, destination = str(entry).split(":")[:2]
    if not source.startswith((".", "/")):
      out[destination] = source
  return out


def _volume_for(path: str, mounts: dict[str, str]) -> str | None:
  """The named volume that actually backs `path` (longest matching mount destination)."""
  target = PurePosixPath(path)
  best = None
  for destination, volume in mounts.items():
    dest = PurePosixPath(destination)
    if target == dest or dest in target.parents:
      if best is None or len(destination) > len(best[0]):
        best = (destination, volume)
  return best[1] if best else None


def test_kafka_log_dir_is_on_the_named_volume():
  compose = _compose()
  kafka = compose["services"]["kafka"]
  log_dir = kafka["environment"].get("KAFKA_LOG_DIRS")
  assert log_dir, "KAFKA_LOG_DIRS unset: the apache/kafka default is under /tmp (container layer)"
  volume = _volume_for(log_dir, _named_mounts(kafka))
  assert volume == "kafkadata", f"Kafka log dir {log_dir} is not backed by the kafkadata volume"
  assert "kafkadata" in compose["volumes"]


def test_analysis_engine_outbox_is_on_a_named_volume():
  compose = _compose()
  config = yaml.safe_load((ROOT / "config" / "transport.yml").read_text())
  outbox = config["transport"]["kafka"]["outbox_path"]
  engine = compose["services"]["analysis-engine"]
  volume = _volume_for(outbox, _named_mounts(engine))
  assert volume is not None, f"publication ledger {outbox} is in the container layer and dies on recreate"
  assert volume in compose["volumes"]


def test_postgres_and_redis_keep_their_data_on_volumes():
  compose = _compose()
  services = compose["services"]
  assert _volume_for("/var/lib/postgresql/data", _named_mounts(services["postgres"])) == "pgdata"
  assert _volume_for("/data", _named_mounts(services["redis"])) == "redisdata"
  assert "--appendonly" in services["redis"]["command"]
