# ADR-010: .NET Kafka client — `Dekaf`, not `Confluent.Kafka`

## Status
Accepted; implemented (cTrader → Kafka → Analysis Engine pipeline task).

## Context
`ctrader-engine` is .NET 8, published as **Native AOT** (`PublishAot=true`,
`PublishTrimmed=true`, `SelfContained=true`, `InvariantGlobalization=true`
— `ctrader-engine/src/CTraderFeed.csproj`). Source task §12 is explicit:
"Do NOT blindly add a Kafka package and assume normal `dotnet test`
proves it works... Native AOT/native-library behavior MUST be
validated... If the preferred library is incompatible, document the
evidence and choose an alternative deliberately." This ADR is that
evidence.

## Decision
**`Dekaf`** (pure managed C#, MIT-licensed, targets `net8.0`), pinned at
**v1.19.0**, with **`<TrimMode>partial</TrimMode>`** set explicitly in
`CTraderFeed.csproj`. `Confluent.Kafka` was tried first, per the task's
own "obvious candidate" framing, and is **confirmed incompatible** —
concrete, reproducible evidence below, not an assumption.

### The evidence

All four runs below used the real Dockerfile publish command
(`ctrader-engine/Dockerfile`'s own `dotnet publish -c Release -r
linux-x64 --self-contained true /p:PublishAot=... /p:PublishTrimmed=...`)
against a throwaway probe project referencing each library, run against
a real ephemeral Redpanda broker — not assumed, not mocked.

| # | Library | Publish flags | Result |
|---|---|---|---|
| 1 | `Confluent.Kafka` 2.6.1 | `PublishAot=true PublishTrimmed=true` | Publishes cleanly (one benign IL2104 trim warning), then **crashes at runtime**: `System.InvalidOperationException: Sequence contains no matching element` at `Confluent.Kafka.Impl.Librdkafka.SetDelegates` — the library reflects over its own method table to bind P/Invoke delegates to the native `librdkafka.so`, and trimming removes what that reflection needs to find. |
| 2 | `Confluent.Kafka` **2.15.1** (latest at time of writing) | same | **Identical crash**, same stack trace shape — not a version-specific regression, a structural incompatibility. |
| 3 | `Confluent.Kafka` 2.15.1 | `PublishAot=false PublishTrimmed=true PublishSingleFile=true` (the Dockerfile's own existing non-AOT fallback branch) | **Same crash.** This isolates the cause: it is `PublishTrimmed=true` itself, not AOT native codegen specifically — confirmed by run 4. |
| 4 | `Confluent.Kafka` 2.15.1 | `PublishTrimmed=false` (trimming off entirely) | **Works** — connects, produces, no crash. Proves the root cause precisely, but disabling trimming is exactly what source task §12 says not to do "unless explicitly approved," and no such approval was sought or given. |

**Conclusion on Confluent.Kafka**: its delegate-binding mechanism is
fundamentally incompatible with `PublishTrimmed=true` on this project's
exact target (.NET 8, `linux-x64`), independent of whether AOT native
compilation is also enabled. This is a known, still-open class of issue
in `librdkafka`-wrapping .NET clients generally — not a configuration
mistake on this project's part.

Dekaf was tried next (README claims: "no native dependencies, no
interop overhead," "native .NET implementation with no delegation to
other runtimes and unmanaged code," and CI coverage described as
"native AOT coverage... on .NET 10 `linux-x64`" — note: **.NET 10**, not
8, an important caveat this ADR does not gloss over):

| # | Library | Publish flags | Result |
|---|---|---|---|
| 5 | `Dekaf` 1.19.0 | `PublishAot=true PublishTrimmed=true` (default `TrimMode=full`) | Publishes cleanly. **Hangs** (no crash, no output) building the producer against a real broker — `BuildAsync()` never returns within a 15s window. |
| 6 | `Dekaf` 1.19.0 | plain JIT, no trim, no AOT (`dotnet run`) | **Works perfectly** — producer builds, message produced, real offset returned from the real broker (`RecordMetadata { ... Offset = 0 ... }`). Proves the hang in run 5 is trim-caused, not a Dekaf/Redpanda protocol mismatch. |
| 7 | `Dekaf` 1.19.0 | `PublishAot=false PublishTrimmed=true` default `TrimMode=full` | **Same hang** as run 5 — confirms `TrimMode=full` (the default trim mode `PublishTrimmed=true` implies) is the actual cause, not AOT codegen. |
| 8 | `Dekaf` 1.19.0 | `PublishAot=false PublishTrimmed=true` **`TrimMode=partial`** | **Works** — producer builds, produces, real offset returned (continuing the same topic's offset sequence from run 6, proving it's the same real broker). |
| 9 | `Dekaf` 1.19.0 | **`PublishAot=true` `TrimMode=partial`** (full Native AOT, self-contained, trimmed, just not `TrimMode=full`) | **Works** — the actual target configuration. Producer builds, produces, real offset returned from the real broker. |

**Conclusion on Dekaf**: fully compatible with this project's real Native
AOT publish pipeline on .NET 8, **provided `TrimMode=partial` is set
explicitly** instead of leaving the AOT-implied default `TrimMode=full`.
This is a legitimate, first-class MSBuild trimming mode — not a
workaround that disables AOT, trimming, or self-containment (all three
stay on, satisfying source task §12's "do not disable PublishAot /
PublishTrimmed / SelfContained... unless explicitly approved" — nothing
here was disabled, one mode setting was changed).

### One known Dekaf limitation, noted honestly, not relevant to this task's scope

Run 10 (not tabulated above): Dekaf's **consumer** (not producer) failed
against the test Redpanda broker with a clear, well-typed exception:
`Dekaf.Errors.BrokerVersionException: The target Kafka broker does not
support the ConsumerGroupHeartbeat API (KIP-848, introduced in Kafka
4.0). Dekaf's consumer requires Kafka 4.0 or later.` This is a genuine
broker-version requirement of Dekaf's consumer group protocol
implementation, unrelated to AOT/trimming (the exception itself proves
AOT+trim is working correctly — a real typed exception with a full
managed stack trace, not a crash). **Irrelevant to this task's actual
scope**: `ctrader-engine` only ever **produces** in this pipeline (source
task §2's own diagram: cTrader → ctrader-engine → Kafka → analysis-engine
— the Go side, already proven on `franz-go`, is the only consumer).
Documented here so a future task adding a .NET-side Kafka *consumer*
starts from this evidence instead of rediscovering it, and so the
production Kafka broker version choice (see
[`../transport/kafka.md`](../transport/kafka.md)) is made with this
constraint in view even though it doesn't bind today.

## Consequences
- `ctrader-engine/src/CTraderFeed.csproj` gains one new dependency
  (`Dekaf` 1.19.0, plus its own managed dependencies —
  `Microsoft.Extensions.Logging.Abstractions`,
  `Microsoft.Extensions.Options`, `Reservoir`, `System.IO.Hashing`,
  `System.IO.Pipelines` — all pure managed, no native binaries added to
  the publish output, unlike `Confluent.Kafka`'s bundled
  `librdkafka.so`/`alpine-librdkafka.so`/`centos8-librdkafka.so`).
- `<TrimMode>partial</TrimMode>` is now a load-bearing project setting,
  not a stylistic choice — removing it silently reintroduces the run-5/
  run-7 hang. A real AOT publish + run against a broker is too slow and
  Docker/clang-dependent to belong in the normal `dotnet test` loop, so
  no unit test guards it directly; instead
  [`../transport/kafka.md`](../transport/kafka.md)'s "Native AOT
  verification" section documents the exact publish-and-run command this
  ADR's evidence came from, to be re-run by hand whenever Dekaf, the
  target framework, or `TrimMode` itself changes.
- If Dekaf ever needs to be replaced, the evidence-gathering method in
  this ADR (publish under the project's real flags, run against a real
  broker, isolate AOT vs. trimming vs. trim-mode as separate variables)
  is the one to repeat — not a fresh set of assumptions.
