# AGENTS.md

Rules for AI agents working on **apexvoid-trading-bot**.

## Tests

The repository has no test suite, goldens or reference models of its own. The only tests are those the owner
asked for in a task; CI runs them (`go test ./...`, `python -m unittest discover -s tests -t .`).
Do not add a test suite, test project, `testdata/` or `fixtures/` directory, or a `pytest`
or `dotnet test` step unless the owner asks for it.

## CI

The **Autotrade Integrity** workflow (`.github/workflows/autotrade-integrity.yml`) checks
that the services build, that the requested tests pass and that the stack file is valid:

| Job | Check |
|-----|-------|
| **Python bot compiles** | `pip install -r requirements.txt`, `python -m compileall -q app tools`, `python -m unittest discover -s tests -t .` |
| **Go analysis engine builds** | `go build ./...`, `go vet ./...`, `go test ./...` |
| **C# ctrader engine builds** | `dotnet build src/CTraderFeed.csproj` |
| **Repository checks** | `docker compose config -q` |

Do not change `.github/workflows/autotrade-integrity.yml` or `.github/workflows/deploy.yml`
unless the user explicitly asks. If CI fails on your PR, fix your code, not the workflow.

## Before you push

```bash
(cd analysis-engine && go build ./... && go vet ./... && go test ./...)
(cd algo-bot && python -m compileall -q app tools && python -m unittest discover -s tests -t .)
(cd ctrader-engine && dotnet build src/CTraderFeed.csproj --nologo)
```

## General PR hygiene

- One focused change per PR; no drive-by refactors.
- Update `CHANGELOG.md` under `## Unreleased` when behavior or operators would notice.
- Do not push to `master` directly; use PRs.
- Do not commit unless the user asks.
- This file lives at **`.agents/AGENTS.md`** (not repo root).
