---
name: Bullhorn shared API user (credential blast radius)
description: One Bullhorn API user (username+password) is shared by multiple apps; changing it impacts all of them and can lock the account. Live password source + the Test Connection trap.
---

# Bullhorn shared API user — credential blast radius

**Rule:** A single Bullhorn API user (`myticasbh1.api`, username + password) is shared by **multiple independent apps**: Scout Genius production, Scout Genius dev, and at least one separate Replit project. Each app has its **own** `client_id` + `redirect_uri` (independent OAuth apps), but they all authenticate with the **same shared username + password**.

**Why:** Changing the shared username or password affects **every** integrating app at once. If even one consumer is left on a stale password, its background poller keeps retrying with the wrong password and trips the Bullhorn account lockout (`failedLoginLockoutThreshold`) — which locks out **all** apps. This is exactly what caused the June 2026 production auth outage: the password was rotated in some places but not the DB the live path reads, and the retries locked the shared account.

**How to apply:**
- Before rotating the shared username/password, inventory **every** consumer and stage the new value **everywhere first**, then rotate/unlock — never unlock while any consumer still holds the old value.
- After a lockout where the password is already verified correct, ask Bullhorn to **unlock only (no password reset)**.
- `client_id` / `redirect_uri` are **per-app** and safe to change for one app without affecting the others (the lockout is tied to username+password, not client).

**Live password source (Scout Genius):** the live auth path reads the password from the DB row `global_settings.bullhorn_password`, NOT the env secret. The `BULLHORN_PASSWORD` secret only seeds a fresh DB — updating the secret does **not** change live auth. To change live: ATS Integration Settings → **Save Settings** (writes `global_settings`), or update the DB row directly. The orange **"Test Connection"** button on that page launches an interactive OAuth flow with a callback Bullhorn never registered (`/ats-integration/oauth/callback` → "Invalid Redirect URI") AND it does not save typed input — ignore it. The background/headless path uses the registered `/bullhorn/oauth/callback` and is what actually works.

**Accelerant to watch:** a background poller retries auth every ~10s (intended ~2 min), so a wrong password can lock the account within minutes. Hardening (correct interval + auth-failure backoff) is the recommended fix so a future credential change can't lock the account so fast.
