# Security

## Threat Model

This is a small, single-tenant bot with a **very narrow** attack surface: it
opens no inbound ports and only initiates outbound connections to Telegram and
Anthropic. Realistic threats, in decreasing order of likelihood:

1. **Credential leak in source control** — the most common real-world incident
   for projects of this shape. Mitigation: strict `.gitignore`, never commit
   `.env`, rotate the bot token if exposure is suspected.
2. **Unauthorized DM commands.** Privileged DM handlers fail closed unless
   `TELEGRAM_OWNER_ID` is configured and matches the sender.
3. **Bot token compromise.** A leaked `TELEGRAM_BOT_TOKEN` grants full control
   of the bot (posting to the channel). Mitigation: treat it as a secret,
   rotate via `@BotFather` `/revoke`.
4. **Host compromise via opportunistic SSH scanning.** Mitigation: SSH
   key-only auth, no password login, non-root user.

Because the bot places **no orders** and receives **no inbound traffic**, there
is no forged-signal or replay vector, and no broker-drain path.

## Secret Inventory

| Secret | Location | Rotation |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `.env` | `@BotFather` → `/revoke`. Re-issue on suspected leak. |
| `TELEGRAM_CHAT_ID` | `.env` | Not secret per se; re-derive if the channel is recreated. |
| `ANTHROPIC_API_KEY` | `.env` | Rotate in the Anthropic console. |
| SSH private key | Operator workstation | Passphrase-protected. Rotate on compromise. |

## Secret Handling

- `.env` has mode `600` and is owned by the deploying user.
- `.env` is listed in `.gitignore`. Before a `git push`, grep for leaks:
  ```bash
  git grep -E '^(TELEGRAM|ANTHROPIC)_' || echo "clean"
  ```
- Never screenshot or paste `.env` or the bot token.

## Network Surface

### Inbound

| Port | Purpose | Exposure |
|---|---|---|
| 22 | SSH | Key-only auth, no passwords |

The bot container publishes **no ports**. There is nothing else to reach.

### Outbound

The host initiates connections to:

- `api.telegram.org` — bot long-polling and message delivery.
- `api.anthropic.com` — chart vision analysis (if enabled).
- `download.docker.com`, `deb.debian.org` — package updates.

No broker/exchange APIs are contacted.

## SSH Hardening

```
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
LoginGraceTime 30
```

Optional: `fail2ban` for the `sshd` jail; restrict port 22 to known source IPs
if you have a static address.

## Docker Security Notes

- The `docker` group is root-equivalent on the host. Acceptable on a
  single-operator box; use `sudo docker` on shared hosts.
- The container runs as root by default. The bot does not need root — adding a
  `USER` directive to the Dockerfile is a good follow-up.

## Supply Chain

- Base image (`python:3.12-slim`) is pinned by tag. For stricter
  reproducibility, pin to a digest and update deliberately.
- Python dependencies are pinned in `webhook/requirements.txt`. Run `pip-audit`
  before bumping.

## Incident Response

If you suspect compromise:

1. Rotate everything in the secret inventory, most-sensitive first.
2. Revoke the bot token (`@BotFather` → `/revoke`).
3. Revoke and replace the SSH key; rebuild the host from a fresh image and
   redeploy from source control rather than trusting a possibly-tampered host.
