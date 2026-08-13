# Security

## Threat Model

Single-tenant trading stack with a **narrow inbound** surface (SSH only) and
outbound connections to Telegram, optional Anthropic, and cTrader Open API.
Realistic threats, in decreasing order of likelihood:

1. **Credential leak in source control** — `.env` secrets (Telegram, cTrader,
   Postgres). Mitigation: strict `.gitignore`, never commit `.env`, rotate
   immediately on exposure.
2. **cTrader token / account compromise** — leaked Open API tokens can trade.
   Mitigation: demo-first profiles, `AUTO_TRADE_REQUIRE_DEMO_ONLY_TOKEN` where
   applicable, rotate refresh tokens via cTrader Playground, host file + Redis
   token mirror.
3. **Unauthorized DM commands.** Privileged DM handlers fail closed unless
   `TELEGRAM_OWNER_ID` matches the sender.
4. **Bot token compromise.** A leaked `TELEGRAM_BOT_TOKEN` can post to VIP/
   public channels. Rotate via `@BotFather` `/revoke`.
5. **Host compromise via SSH scanning.** Key-only auth, no password login,
   non-root deploy user.

Autonomous execution is intentionally gated (config health, spread/news,
opposing room, stop envelope). Fail closed rather than guessing.

## Secret Inventory

| Secret | Location | Rotation |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `.env` | `@BotFather` → `/revoke` |
| `SIGNAL_VIP_CHANNEL_ID` / public id | `.env` | Re-derive if channel recreated |
| `POSTGRES_PASSWORD` / `DATABASE_URL` | `.env` | Rotate DB role; update DSN |
| `CTRADER_CLIENT_SECRET` / access / refresh | `.env` + Redis/file mirror | Re-auth via cTrader Playground |
| `ANTHROPIC_API_KEY` | `.env` | Rotate in Anthropic console |
| SSH private key | Operator workstation | Passphrase-protected; rotate on compromise |

## Secret Handling

- `.env` has mode `600` and is owned by the deploying user.
- `.env` is listed in `.gitignore`. Before a `git push`, grep for leaks:
  ```bash
  git grep -E '^(TELEGRAM_|CTRADER_|POSTGRES_|ANTHROPIC_|DATABASE_URL)' || echo "clean"
  ```
- Never screenshot or paste `.env` or bot/cTrader tokens.
- Runtime manifest is secret-safe (no credential material in
  `resolved-runtime.json`).

## Network Surface

### Inbound

| Port | Purpose | Exposure |
|---|---|---|
| 22 | SSH | Key-only auth, no passwords |

Compose services publish **no** application ports to the host by default
(Postgres/Redis stay on the internal network).

### Outbound

- `api.telegram.org` — long-polling and message delivery
- `api.anthropic.com` — chart vision (if enabled)
- cTrader Open API hosts (`demo.ctraderapi.com` / live) — feed + execution
- OS / Docker package mirrors as needed for host maintenance

## Hardening Checklist

- [ ] `.env` mode `600`, not world-readable
- [ ] SSH key-only; disable password auth
- [ ] Owner ID set; test that non-owner DMs are ignored
- [ ] Auto-trade profile appropriate (`demo_eval` vs live)
- [ ] Backups of Postgres dumps stored off-host
- [ ] Log retention bounded (`LOG_RETENTION_DAYS`)
