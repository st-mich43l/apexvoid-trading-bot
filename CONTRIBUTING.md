# Contributing

This is a personal project without external contributors. These notes
document the internal workflow for keeping the repository clean.

## Branching

- `main` — deployable. Only merge-ready changes land here.
- `feature/<short-slug>` — work in progress for a single logical change.

Do not commit directly to `main`. Every change goes through a branch, even
trivial doc edits, so the history is reviewable.

## Commit Messages

Follow the conventional commits prefix:

```
<type>(<scope>): <subject>

<body>
```

Types: `feat`, `fix`, `docs`, `refactor`, `chore`, `ops`.

Scopes used in this repo: `bot`, `chart`, `pips`, `docs`, `infra`.

Examples:

```
feat(bot): add cancel-by-channel-reply handler
fix(pips): correct week boundary in period range
docs(deployment): document owner-only DM setup
ops(infra): drop exposed ports from compose
```

## Pre-push Checks

Before pushing a branch, verify:

```bash
# Secrets are not staged
git diff --cached | grep -E '(TELEGRAM_BOT_TOKEN|ANTHROPIC_API_KEY|BEGIN.*PRIVATE KEY)' \
  && echo "SECRET DETECTED - do not push" \
  || echo "clean"

# Python syntax
python3 -m compileall -q webhook/
```

## Updating Documentation

Docs live under `docs/` and follow these conventions:

- Use ATX-style headers (`#`, `##`).
- Line-wrap at ~80 columns for readability in plain editors.
- Inline commands use backticks; multi-line examples use fenced blocks
  with a language hint (```` ```bash ````, ```` ```python ````, etc.).
- Prefer tables for structured reference material.
- Cross-link documents with relative paths (`docs/deployment.md`).

When changing deployment steps, update both `docs/deployment.md` and the
"Quick Start" block in `README.md` if applicable.

Every behavior, configuration, deployment, or operator-facing change must also
add a concise entry under `Unreleased` in `CHANGELOG.md` in the same pull
request. Pure test-only and internal comment changes do not need an entry.

## What Not to Commit

- `.env` and anything else containing a real secret.
- `data/` and any `*.db` file.
- Editor/OS metadata (`.vscode/`, `.idea/`, `.DS_Store`).

All of the above are covered by `.gitignore`. If a file of any of these
types appears in `git status`, something is wrong.

## Release / Deploy

"Release" here means deploying `main` to the production host:

```bash
ssh <user>@<host>
cd ~/xau-signal-bot
git pull --ff-only
docker compose up -d --build
docker compose logs -f bot     # watch for startup messages
```

There is no tag-based release process; the host simply tracks `main`.

## Backing Out a Bad Deploy

```bash
git log --oneline -10              # find the previous good commit
git reset --hard <sha>
docker compose up -d --build
```

If the bad change corrupted `signals.db`, restore from the most recent
backup (see `docs/operations.md`).
