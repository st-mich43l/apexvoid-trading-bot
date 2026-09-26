"""S14A: a Redis client for dry-running the live policy with zero side effects.

The live worker cycle is client-injectable. The shadow gives it an
``OverlayRedis`` instead of the real client:

* **reads** of a key the dry run has not written go to the real Redis, through
  a guard that *raises* on any command outside ``READ_COMMANDS`` (so a bug can
  only fail the dry run, never write to production);
* **writes** land in process memory. The first write to a key copies its current
  value in (strings, hashes, sets, sorted sets, lists; streams start empty
  because the dry run only needs to see its own entries), so read-modify-write
  logic behaves exactly as live;
* deletes are tombstones; scans see the union of both sides;
* anything else (Lua, pub/sub, consumer groups, unknown commands) raises
  ``ShadowSideEffectError``. The plan-publish and setup-transition paths already
  fall back to non-atomic commands when ``eval`` raises and the client carries
  ``_apexvoid_allow_non_atomic_test_fallback``; the overlay sets that flag.

``is_shadow_overlay`` is the only way callers may relax a live-only check (the
authority fence): the relaxation is honoured only for this class, whose writes
can never reach Redis.
"""

from __future__ import annotations

import fnmatch
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class ShadowSideEffectError(RuntimeError):
  """A dry run tried to do something with a side effect (or unsupported)."""


READ_COMMANDS = frozenset({
  "get", "mget", "exists", "ttl", "pttl", "type", "strlen",
  "hget", "hgetall", "hmget", "hlen", "hexists", "hkeys", "hvals",
  "smembers", "sismember", "scard",
  "zrange", "zrevrange", "zrangebyscore", "zrevrangebyscore", "zscore", "zcard", "zcount", "zrank",
  "lrange", "llen", "lindex",
  "xrange", "xrevrange", "xlen",
  "scan_iter", "keys",
})


class ReadOnlyRealRedis:
  """The only handle the overlay has on production Redis."""

  def __init__(self, real: Any):
    self._real = real
    self.calls: Counter[str] = Counter()

  def __getattr__(self, name: str):
    if name not in READ_COMMANDS:
      raise ShadowSideEffectError(f"shadow dry run attempted a non-read Redis command on the real client: {name}")
    attr = getattr(self._real, name)
    self.calls[name] += 1
    return attr


def _text(value: Any) -> Any:
  return value.decode() if isinstance(value, bytes) else value


def _lua_unavailable(*_args: Any, **_kwargs: Any):
  raise ShadowSideEffectError("Lua is unavailable in the shadow overlay (callers fall back to non-atomic commands)")


class OverlayRedis:
  """See the module docstring. Values are stored decoded (str), matching the
  ``decode_responses=True`` production client."""

  _apexvoid_allow_non_atomic_test_fallback = True
  _apexvoid_shadow_overlay = True

  def __init__(self, real: Any, *, clock: Any = time.time):
    self._real = ReadOnlyRealRedis(real)
    self._clock = clock
    self._data: dict[str, Any] = {}
    self._expires: dict[str, float] = {}
    self._deleted: set[str] = set()
    self.writes: Counter[str] = Counter()
    self._stream_seq = 0

  # ---- introspection (used for audit, never by the live code) ----------------
  @property
  def real_read_calls(self) -> Counter[str]:
    return self._real.calls

  @property
  def dirty_keys(self) -> list[str]:
    return sorted(k for k in self._data if self._alive(k))

  # ---- key state -----------------------------------------------------------------
  def _alive(self, key: str) -> bool:
    exp = self._expires.get(key)
    if exp is not None and exp <= self._clock():
      self._data.pop(key, None)
      self._expires.pop(key, None)
      self._deleted.add(key)
      return False
    return key in self._data

  def _local_state(self, key: str) -> str:
    """'local' (overlay owns it), 'gone' (deleted/expired here), 'real'."""
    if self._alive(key):
      return "local"
    if key in self._deleted:
      return "gone"
    return "real"

  async def _hydrate(self, key: str, kind: str) -> Any:
    """Return the overlay's mutable value for ``key``, copying it in from the
    real Redis on first write. ``kind`` is the container the caller needs."""
    state = self._local_state(key)
    if state == "local":
      return self._data[key]
    empty: dict[str, Any] = {"str": None, "hash": {}, "set": set(), "zset": {}, "list": [], "stream": []}
    value = empty[kind]
    if state == "real":
      real_type = _text(await self._real.type(key))
      if real_type != "none" and real_type != {"str": "string", "hash": "hash", "set": "set", "zset": "zset", "list": "list", "stream": "stream"}[kind]:
        raise ShadowSideEffectError(f"WRONGTYPE for {key}: real is {real_type}, dry run wants {kind}")
      if real_type == "string":
        value = _text(await self._real.get(key))
      elif real_type == "hash":
        value = {_text(k): _text(v) for k, v in (await self._real.hgetall(key)).items()}
      elif real_type == "set":
        value = {_text(m) for m in await self._real.smembers(key)}
      elif real_type == "zset":
        value = {_text(m): float(s) for m, s in await self._real.zrange(key, 0, -1, withscores=True)}
      elif real_type == "list":
        value = [_text(v) for v in await self._real.lrange(key, 0, -1)]
      if real_type not in ("none", "stream"):
        pttl = await self._real.pttl(key)
        if isinstance(pttl, int) and pttl > 0:
          self._expires[key] = self._clock() + pttl / 1000.0
    self._deleted.discard(key)
    self._data[key] = value
    return value

  def _touch(self, name: str) -> None:
    self.writes[name] += 1

  # ---- strings ---------------------------------------------------------------------
  async def get(self, key: str):
    state = self._local_state(key)
    if state == "local":
      return self._data[key]
    return None if state == "gone" else await self._real.get(key)

  async def mget(self, keys, *more):
    keys = [*keys, *more] if isinstance(keys, (list, tuple)) else [keys, *more]
    return [await self.get(k) for k in keys]

  async def set(self, key: str, value: Any, ex: int | None = None, px: int | None = None, nx: bool = False, xx: bool = False, keepttl: bool = False):
    exists = (await self.exists(key)) > 0
    if (nx and exists) or (xx and not exists):
      return None
    self._touch("set")
    self._data[key] = str(value) if not isinstance(value, str) else value
    self._deleted.discard(key)
    if ex is not None:
      self._expires[key] = self._clock() + float(ex)
    elif px is not None:
      self._expires[key] = self._clock() + px / 1000.0
    elif not keepttl:
      self._expires.pop(key, None)
    return True

  async def setex(self, key: str, time_: int, value: Any):
    return await self.set(key, value, ex=time_)

  async def incrby(self, key: str, amount: int = 1) -> int:
    self._touch("incrby")
    current = await self._hydrate(key, "str")
    new = int(current or 0) + int(amount)
    self._data[key] = str(new)
    return new

  async def incr(self, key: str, amount: int = 1) -> int:
    return await self.incrby(key, amount)

  async def exists(self, *keys: str) -> int:
    count = 0
    for key in keys:
      state = self._local_state(key)
      count += 1 if state == "local" else 0 if state == "gone" else int(await self._real.exists(key))
    return count

  async def delete(self, *keys: str) -> int:
    removed = 0
    for key in keys:
      if await self.exists(key):
        removed += 1
      self._touch("delete")
      self._data.pop(key, None)
      self._expires.pop(key, None)
      self._deleted.add(key)
    return removed

  async def expire(self, key: str, seconds: int) -> bool:
    if not await self.exists(key):
      return False
    self._touch("expire")
    await self._hydrate(key, await self._kind_of(key))
    self._expires[key] = self._clock() + float(seconds)
    return True

  async def _kind_of(self, key: str) -> str:
    if self._local_state(key) == "local":
      v = self._data[key]
      return "hash" if isinstance(v, dict) and not _is_zset(v) else "zset" if isinstance(v, dict) else "set" if isinstance(v, set) else "list" if isinstance(v, list) else "str"
    real_type = _text(await self._real.type(key))
    return {"string": "str", "hash": "hash", "set": "set", "zset": "zset", "list": "list", "stream": "stream"}.get(real_type, "str")

  async def ttl(self, key: str) -> int:
    state = self._local_state(key)
    if state == "gone":
      return -2
    if state == "local":
      exp = self._expires.get(key)
      return -1 if exp is None else max(0, int(exp - self._clock()))
    return await self._real.ttl(key)

  async def pttl(self, key: str) -> int:
    ttl = await self.ttl(key)
    return ttl if ttl < 0 else ttl * 1000

  async def type(self, key: str) -> str:
    state = self._local_state(key)
    if state == "gone":
      return "none"
    if state == "real":
      return _text(await self._real.type(key))
    return {"str": "string", "hash": "hash", "set": "set", "zset": "zset", "list": "list"}[await self._kind_of(key)]

  # ---- hashes ----------------------------------------------------------------------
  async def hget(self, key: str, field: str):
    state = self._local_state(key)
    if state == "local":
      return self._data[key].get(field)
    return None if state == "gone" else await self._real.hget(key, field)

  async def hmget(self, key: str, fields, *more):
    fields = [*fields, *more] if isinstance(fields, (list, tuple)) else [fields, *more]
    return [await self.hget(key, f) for f in fields]

  async def hgetall(self, key: str) -> dict:
    state = self._local_state(key)
    if state == "local":
      return dict(self._data[key])
    return {} if state == "gone" else await self._real.hgetall(key)

  async def hexists(self, key: str, field: str) -> bool:
    return (await self.hget(key, field)) is not None

  async def hlen(self, key: str) -> int:
    return len(await self.hgetall(key))

  async def hkeys(self, key: str):
    return list((await self.hgetall(key)).keys())

  async def hset(self, key: str, field: str | None = None, value: Any = None, mapping: dict | None = None) -> int:
    self._touch("hset")
    h = await self._hydrate(key, "hash")
    items = dict(mapping or {})
    if field is not None:
      items[field] = value
    added = sum(1 for f in items if f not in h)
    h.update({str(f): str(v) for f, v in items.items()})
    return added

  async def hdel(self, key: str, *fields: str) -> int:
    self._touch("hdel")
    h = await self._hydrate(key, "hash")
    return sum(1 for f in fields if h.pop(f, None) is not None)

  async def hincrby(self, key: str, field: str, amount: int = 1) -> int:
    self._touch("hincrby")
    h = await self._hydrate(key, "hash")
    h[field] = str(int(h.get(field, 0)) + int(amount))
    return int(h[field])

  async def hincrbyfloat(self, key: str, field: str, amount: float = 1.0) -> float:
    self._touch("hincrbyfloat")
    h = await self._hydrate(key, "hash")
    h[field] = repr(float(h.get(field, 0)) + float(amount))
    return float(h[field])

  # ---- sets ------------------------------------------------------------------------
  async def smembers(self, key: str) -> set:
    state = self._local_state(key)
    if state == "local":
      return set(self._data[key])
    return set() if state == "gone" else set(await self._real.smembers(key))

  async def sismember(self, key: str, member: str) -> bool:
    return member in await self.smembers(key)

  async def scard(self, key: str) -> int:
    return len(await self.smembers(key))

  async def sadd(self, key: str, *members: str) -> int:
    self._touch("sadd")
    s = await self._hydrate(key, "set")
    before = len(s)
    s.update(members)
    return len(s) - before

  async def srem(self, key: str, *members: str) -> int:
    self._touch("srem")
    s = await self._hydrate(key, "set")
    before = len(s)
    s.difference_update(members)
    return before - len(s)

  # ---- sorted sets -----------------------------------------------------------------
  async def _zitems(self, key: str) -> list[tuple[str, float]]:
    state = self._local_state(key)
    if state == "local":
      z = self._data[key]
    elif state == "gone":
      z = {}
    else:
      z = {_text(m): float(s) for m, s in await self._real.zrange(key, 0, -1, withscores=True)}
    return sorted(z.items(), key=lambda kv: (kv[1], kv[0]))

  @staticmethod
  def _slice(items: list, start: int, end: int) -> list:
    n = len(items)
    start = start + n if start < 0 else start
    end = end + n if end < 0 else end
    return items[max(0, start):end + 1]

  async def zrange(self, key: str, start: int, end: int, withscores: bool = False):
    if self._local_state(key) == "real":
      return await self._real.zrange(key, start, end, withscores=withscores)
    part = self._slice(await self._zitems(key), start, end)
    return part if withscores else [m for m, _ in part]

  async def zrevrange(self, key: str, start: int, end: int, withscores: bool = False):
    if self._local_state(key) == "real":
      return await self._real.zrevrange(key, start, end, withscores=withscores)
    part = self._slice(list(reversed(await self._zitems(key))), start, end)
    return part if withscores else [m for m, _ in part]

  async def zrangebyscore(self, key: str, min: Any, max: Any, start: int | None = None, num: int | None = None, withscores: bool = False):
    if self._local_state(key) == "real":
      return await self._real.zrangebyscore(key, min, max, start=start, num=num, withscores=withscores)
    lo = float("-inf") if min in ("-inf", "-") else float(min)
    hi = float("inf") if max in ("+inf", "inf", "+") else float(max)
    part = [(m, s) for m, s in await self._zitems(key) if lo <= s <= hi]
    if start is not None and num is not None:
      part = part[start:start + num]
    return part if withscores else [m for m, _ in part]

  async def zscore(self, key: str, member: str):
    for m, s in await self._zitems(key):
      if m == member:
        return s
    return None

  async def zcard(self, key: str) -> int:
    return len(await self._zitems(key))

  async def zadd(self, key: str, mapping: dict, nx: bool = False, xx: bool = False, gt: bool = False, lt: bool = False, ch: bool = False) -> int:
    self._touch("zadd")
    z = await self._hydrate(key, "zset")
    changed = 0
    for member, score in mapping.items():
      score = float(score)
      present = member in z
      if (nx and present) or (xx and not present):
        continue
      if present and ((gt and score <= z[member]) or (lt and score >= z[member])):
        continue
      if not present or z[member] != score:
        changed += 1 if (not present or ch) else 0
      z[member] = score
    return changed

  async def zrem(self, key: str, *members: str) -> int:
    self._touch("zrem")
    z = await self._hydrate(key, "zset")
    return sum(1 for m in members if z.pop(m, None) is not None)

  async def zremrangebyscore(self, key: str, min: Any, max: Any) -> int:
    self._touch("zremrangebyscore")
    z = await self._hydrate(key, "zset")
    lo = float("-inf") if min in ("-inf", "-") else float(min)
    hi = float("inf") if max in ("+inf", "inf", "+") else float(max)
    doomed = [m for m, s in z.items() if lo <= s <= hi]
    for m in doomed:
      del z[m]
    return len(doomed)

  # ---- lists -----------------------------------------------------------------------
  async def lrange(self, key: str, start: int, end: int):
    state = self._local_state(key)
    if state == "local":
      return self._slice(list(self._data[key]), start, end)
    return [] if state == "gone" else await self._real.lrange(key, start, end)

  async def llen(self, key: str) -> int:
    return len(await self.lrange(key, 0, -1))

  async def rpush(self, key: str, *values: Any) -> int:
    self._touch("rpush")
    lst = await self._hydrate(key, "list")
    lst.extend(str(v) for v in values)
    return len(lst)

  async def lpush(self, key: str, *values: Any) -> int:
    self._touch("lpush")
    lst = await self._hydrate(key, "list")
    for v in values:
      lst.insert(0, str(v))
    return len(lst)

  async def ltrim(self, key: str, start: int, end: int) -> bool:
    self._touch("ltrim")
    lst = await self._hydrate(key, "list")
    lst[:] = self._slice(lst, start, end)
    return True

  # ---- streams (overlay-only: the dry run sees just its own entries) ---------------
  async def xadd(self, name: str, fields: dict, id: str = "*", maxlen: int | None = None, approximate: bool = True) -> str:
    self._touch("xadd")
    stream = await self._hydrate(name, "stream")
    self._stream_seq += 1
    entry_id = f"{int(self._clock() * 1000)}-{self._stream_seq}" if id == "*" else id
    stream.append((entry_id, {str(k): str(v) for k, v in fields.items()}))
    if maxlen is not None and len(stream) > maxlen:
      del stream[:len(stream) - maxlen]
    return entry_id

  async def xlen(self, name: str) -> int:
    return len(self._data[name]) if self._local_state(name) == "local" else 0

  async def xrange(self, name: str, min: str = "-", max: str = "+", count: int | None = None):
    entries = list(self._data[name]) if self._local_state(name) == "local" else []
    return entries[:count] if count else entries

  # ---- scanning --------------------------------------------------------------------
  async def scan_iter(self, match: str | None = None, count: int | None = None):
    seen: set[str] = set()
    async for key in self._real.scan_iter(match=match, count=count):
      key = _text(key)
      if self._local_state(key) != "gone" and key not in self._data:
        seen.add(key)
    for key in list(self._data):
      if self._alive(key) and (match is None or fnmatch.fnmatchcase(key, match)):
        seen.add(key)
    for key in sorted(seen):
      yield key

  async def keys(self, pattern: str = "*"):
    return [k async for k in self.scan_iter(match=pattern)]

  # ---- pipelines / scripting / everything else ---------------------------------------
  def pipeline(self, transaction: bool = True) -> "_OverlayPipeline":
    return _OverlayPipeline(self)

  eval = _lua_unavailable
  evalsha = _lua_unavailable
  script_load = _lua_unavailable

  def __getattr__(self, name: str):
    # Reached only for commands the overlay does not implement: refuse loudly.
    if name.startswith("__"):
      raise AttributeError(name)
    raise ShadowSideEffectError(f"shadow overlay does not support Redis command {name!r}")


def _is_zset(value: dict) -> bool:
  return bool(value) and all(isinstance(v, float) for v in value.values())


class _OverlayPipeline:
  """Queues commands and runs them in order against the overlay."""

  def __init__(self, overlay: OverlayRedis):
    self._overlay = overlay
    self._queue: list[tuple[str, tuple, dict]] = []

  def __getattr__(self, name: str):
    if name.startswith("__"):
      raise AttributeError(name)
    if not hasattr(type(self._overlay), name):
      raise ShadowSideEffectError(f"shadow overlay pipeline does not support {name!r}")

    def queue(*args: Any, **kwargs: Any) -> "_OverlayPipeline":
      self._queue.append((name, args, kwargs))
      return self
    return queue

  async def execute(self, raise_on_error: bool = True) -> list:
    queued, self._queue = self._queue, []
    return [await getattr(self._overlay, name)(*args, **kwargs) for name, args, kwargs in queued]

  async def __aenter__(self) -> "_OverlayPipeline":
    return self

  async def __aexit__(self, *_exc: Any) -> None:
    self._queue.clear()


def is_shadow_overlay(client: Any) -> bool:
  return isinstance(client, OverlayRedis)


@contextmanager
def dry_run_context(overlay: OverlayRedis) -> Iterator[None]:
  """Everything inside sees the overlay as *the* Redis client and a read-only
  PostgreSQL. Context-local: concurrent live tasks are unaffected."""
  from app.persistence import redis_state, store
  with redis_state.client_override(overlay), store.readonly_db():
    yield
