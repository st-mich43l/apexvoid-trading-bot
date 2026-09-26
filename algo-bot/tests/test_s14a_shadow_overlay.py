"""S14A overlay: reads pass through, writes never leave the process."""

from __future__ import annotations

import asyncio

import fakeredis
import pytest

from app.analysis_client.shadow_overlay import (
  READ_COMMANDS,
  OverlayRedis,
  ReadOnlyRealRedis,
  ShadowSideEffectError,
  is_shadow_overlay,
)
from app.persistence import redis_state

pytestmark = pytest.mark.no_database


class RecordingReal:
  """A real-looking client that logs every command reaching it."""

  def __init__(self):
    self.inner = fakeredis.FakeAsyncRedis(decode_responses=True)
    self.commands: list[str] = []

  def __getattr__(self, name):
    attr = getattr(self.inner, name)
    if not callable(attr):
      return attr
    if name == "scan_iter":
      def scan(*a, **k):
        self.commands.append(name)
        return attr(*a, **k)
      return scan

    async def call(*a, **k):
      self.commands.append(name)
      return await attr(*a, **k)
    return call

  async def dump(self):
    out = {}
    async for key in self.inner.scan_iter("*"):
      kind = await self.inner.type(key)
      value = {
        "string": lambda: self.inner.get(key),
        "hash": lambda: self.inner.hgetall(key),
        "set": lambda: self.inner.smembers(key),
        "zset": lambda: self.inner.zrange(key, 0, -1, withscores=True),
        "list": lambda: self.inner.lrange(key, 0, -1),
        "stream": lambda: self.inner.xrange(key),
      }[kind]
      out[key] = (kind, await value(), await self.inner.ttl(key) >= 0)
    return out


@pytest.fixture
def real(event_loop):
  client = RecordingReal()

  async def seed():
    await client.inner.set("s", "1")
    await client.inner.set("ttl", "x", ex=100)
    await client.inner.hset("h", mapping={"a": "1", "b": "2"})
    await client.inner.sadd("set", "m1", "m2")
    await client.inner.zadd("z", {"lo": 1.0, "hi": 3.0, "mid": 2.0})
    await client.inner.rpush("l", "x", "y", "z")
    await client.inner.xadd("stream", {"k": "v"})

  event_loop.run_until_complete(seed())
  client.commands.clear()
  return client


@pytest.mark.asyncio
async def test_reads_pass_through_without_copying(real):
  o = OverlayRedis(real)
  assert await o.get("s") == "1" and await o.exists("s", "nope") == 1
  assert await o.hgetall("h") == {"a": "1", "b": "2"} and await o.hget("h", "a") == "1"
  assert await o.smembers("set") == {"m1", "m2"} and await o.sismember("set", "m1")
  assert await o.zrevrange("z", 0, 1, withscores=True) == [("hi", 3.0), ("mid", 2.0)]
  assert await o.zrange("z", 0, -1) == ["lo", "mid", "hi"]
  assert await o.lrange("l", 0, -1) == ["x", "y", "z"] and await o.llen("l") == 3
  assert await o.type("h") == "hash" and await o.ttl("ttl") > 0
  assert o.dirty_keys == [] and not o.writes


@pytest.mark.asyncio
async def test_every_write_stays_in_the_overlay_and_reads_back(real):
  before = await real.dump()
  o = OverlayRedis(real)
  await o.set("s", "9")
  await o.set("new", "v", ex=50)
  assert await o.incrby("counter", 3) == 3 and await o.incr("counter") == 4
  await o.hset("h", "c", "3")
  assert await o.hincrby("h", "a", 5) == 6 and await o.hincrby("fresh", "f", 1) == 1
  await o.sadd("set", "m3")
  await o.srem("set", "m1")
  await o.zadd("z", {"top": 9.0})
  await o.zrem("z", "lo")
  await o.rpush("l", "w")
  await o.ltrim("l", 1, -1)
  assert await o.get("s") == "9" and await o.get("new") == "v" and await o.get("counter") == "4"
  assert await o.hgetall("h") == {"a": "6", "b": "2", "c": "3"}
  assert await o.smembers("set") == {"m2", "m3"}
  assert await o.zrange("z", 0, -1) == ["mid", "hi", "top"]
  assert await o.lrange("l", 0, -1) == ["y", "z", "w"]
  assert await real.dump() == before              # production is byte-for-byte untouched


@pytest.mark.asyncio
async def test_first_write_hydrates_value_and_ttl_so_read_modify_write_matches_live(real):
  o = OverlayRedis(real)
  assert await o.hincrby("h", "a", 1) == 2         # copied {"a": "1"} first, then incremented
  await o.expire("s", 30)
  assert 0 < await o.ttl("s") <= 30
  assert 0 < await o.ttl("ttl") <= 100


@pytest.mark.asyncio
async def test_delete_is_a_tombstone_and_scan_sees_the_union(real):
  o = OverlayRedis(real)
  assert await o.delete("s", "missing") == 1
  assert await o.exists("s") == 0 and await o.get("s") is None
  await o.set("only_overlay", "1")
  keys = [k async for k in o.scan_iter(match="*")]
  assert "s" not in keys and "only_overlay" in keys and "h" in keys
  assert await real.inner.get("s") == "1"          # still there for the live system


@pytest.mark.asyncio
async def test_nx_xx_and_expiry_semantics(real):
  o = OverlayRedis(real, clock=lambda: 1000.0)
  assert await o.set("s", "no", nx=True) is None    # exists in real
  assert await o.set("brand", "yes", nx=True) is True
  assert await o.set("ghost", "v", xx=True) is None
  clock = {"t": 1000.0}
  o = OverlayRedis(real, clock=lambda: clock["t"])
  await o.set("short", "v", ex=5)
  clock["t"] += 6
  assert await o.get("short") is None and await o.exists("short") == 0


@pytest.mark.asyncio
async def test_streams_are_overlay_only(real):
  o = OverlayRedis(real)
  entry = await o.xadd("stream", {"payload": "p"}, maxlen=10, approximate=True)
  assert await o.xlen("stream") == 1 and (await o.xrange("stream"))[0][0] == entry
  assert await real.inner.xlen("stream") == 1      # untouched: the real entry only


@pytest.mark.asyncio
async def test_pipeline_executes_in_order_against_the_overlay(real):
  o = OverlayRedis(real)
  pipe = o.pipeline(transaction=True)
  pipe.set("p1", "a", ex=10).set("p2", "b").xadd("ps", {"payload": "x"}, maxlen=5, approximate=True)
  results = await pipe.execute()
  assert results[0] is True and results[1] is True and isinstance(results[2], str)
  assert await o.get("p1") == "a" and await real.inner.get("p1") is None


@pytest.mark.asyncio
async def test_unsupported_commands_refuse_loudly_instead_of_touching_anything(real):
  o = OverlayRedis(real)
  for name, args in (("publish", ("ch", "m")), ("xgroup_create", ("s", "g")), ("flushdb", ()), ("eval", ("return 1", 0)), ("script_load", ("x",))):
    with pytest.raises(ShadowSideEffectError):
      result = getattr(o, name)(*args)
      if asyncio.iscoroutine(result):
        await result
  with pytest.raises(ShadowSideEffectError):
    o.pipeline().publish("ch", "m")
  assert not real.commands or set(real.commands) <= READ_COMMANDS


@pytest.mark.asyncio
async def test_wrong_type_writes_fail_instead_of_corrupting(real):
  o = OverlayRedis(real)
  with pytest.raises(ShadowSideEffectError):
    await o.hset("s", "f", "v")                     # 's' is a string in real


@pytest.mark.asyncio
async def test_only_read_commands_ever_reach_the_real_client(real):
  o = OverlayRedis(real)
  await o.set("s", "9")
  await o.hincrby("h", "a", 1)
  await o.zadd("z", {"x": 1.0})
  await o.expire("l", 20)
  await o.delete("set")
  await o.xadd("stream", {"a": "b"})
  async for _ in o.scan_iter(match="*"):
    pass
  assert real.commands and set(real.commands) <= READ_COMMANDS


@pytest.mark.asyncio
async def test_read_only_guard_raises_on_any_write(real):
  guard = ReadOnlyRealRedis(real)
  for name in ("set", "delete", "hset", "xadd", "eval", "publish", "expire", "flushall"):
    with pytest.raises(ShadowSideEffectError):
      getattr(guard, name)
  assert await guard.get("s") == "1"


def test_overlay_declares_the_non_atomic_fallback_and_is_identified():
  o = OverlayRedis(fakeredis.FakeAsyncRedis())
  assert getattr(o, "_apexvoid_allow_non_atomic_test_fallback") is True
  assert is_shadow_overlay(o) and not is_shadow_overlay(fakeredis.FakeAsyncRedis())


@pytest.mark.asyncio
async def test_client_override_is_context_local(real):
  o = OverlayRedis(real)
  seen = {}

  async def in_dry_run():
    with redis_state.client_override(o):
      await asyncio.sleep(0.01)
      seen["dry"] = redis_state.get_client()

  async def concurrent_live():
    await asyncio.sleep(0.005)
    seen["live"] = redis_state.get_client()

  await asyncio.gather(in_dry_run(), concurrent_live())
  assert seen["dry"] is o and seen["live"] is not o
  assert redis_state.get_client() is not o          # restored after the block
