# Bullhorn API Connection Guide (for another agent / project)

**TL;DR — Problem → Fix → Benefit**
- **Problem:** A new agent/project needs to authenticate to Bullhorn ATS/CRM and make REST calls, but Bullhorn's OAuth 2.0 flow has several non-obvious steps and gotchas.
- **Fix:** This guide documents the exact 4-step headless OAuth flow Scout Genius uses, where every credential comes from, the precise endpoints, and a copy-paste Python reference that works end to end.
- **Benefit:** The other agent can stand up a working Bullhorn connection from scratch without trial-and-error, and knows the traps (whitelisted redirect URI, shared API user, HTML-means-failure) up front.

> Source of truth in this codebase: `bullhorn_service/_core.py` (endpoints + credential loading) and `bullhorn_service/auth.py` (`_direct_login`). This guide mirrors that working implementation.

---

## 1. What Bullhorn auth actually is

Bullhorn uses **OAuth 2.0**, but for server-to-server automation you use the **headless ("direct") login** variant: you POST/GET the username + password straight to the OAuth `authorize` endpoint and read the authorization `code` out of the redirect, instead of bouncing a human through a browser.

The flow produces a **`BhRestToken`** (the session token you put on every API call) plus a **`restUrl`** (the data-center-specific base URL for your account). All real API calls use those two values.

Four steps:

1. **Discover endpoints** — find the OAuth + REST URLs for this account's data center.
2. **Get an authorization `code`** — call `authorize` with username/password, read `code` from the 302 redirect.
3. **Exchange `code` for an `access_token`** — POST to the `token` endpoint.
4. **Exchange `access_token` for a `BhRestToken` + `restUrl`** — POST to the REST `login` endpoint.

Then call the API: `GET {restUrl}search/JobOrder?BhRestToken={token}&...`

---

## 2. What you need before you start

### 2.1 Four credentials (per Bullhorn account)
| Credential | What it is |
|---|---|
| `client_id` | OAuth app client ID (issued by Bullhorn Support) |
| `client_secret` | OAuth app client secret (issued by Bullhorn Support) |
| `username` | API user login (e.g. `xxxxx.api`) |
| `password` | API user password |

In **this** project these live in the DB, not env vars — table `global_settings` (model `GlobalSettings`), keys: `bullhorn_client_id`, `bullhorn_client_secret`, `bullhorn_username`, `bullhorn_password` (see `_core.py::_load_credentials`). A new project can store them however it wants (DB row, Replit Secret, etc.) — just don't hardcode them in source.

### 2.2 A whitelisted redirect URI (the #1 gotcha)
The `redirect_uri` you send to `authorize` **must be whitelisted by Bullhorn Support for your `client_id`, and must match byte-for-byte between the `authorize` call and the `token` call.** If it isn't whitelisted, OAuth returns an error instead of a code.

- This project builds it as `{OAUTH_REDIRECT_BASE_URL}/bullhorn/oauth/callback` (env/secret `OAUTH_REDIRECT_BASE_URL`).
- A new project needs **its own** redirect URI whitelisted by Bullhorn Support (email them the exact URL). You never actually have to *serve* that callback route for the headless flow — the code is read straight from the 302 Location header — but Bullhorn still validates the value against the whitelist.

### 2.3 Which API generation: Legacy vs "Bullhorn One"
Two endpoint sets exist. This project toggles via env var `BULLHORN_USE_NEW_API` (`true` = Bullhorn One fixed endpoints; default `false` = legacy with dynamic discovery).

- **Legacy (default):** discover the per-account OAuth/REST URLs at runtime via the `loginInfo` endpoint.
- **Bullhorn One:** use fixed endpoints Bullhorn Support gives you (no discovery). The data-center suffix (e.g. `dcc900`, `rest45`) is account-specific — get yours from Support, don't copy another account's.

---

## 3. The exact endpoints

```
# Legacy discovery (default path):
LEGACY_LOGIN_INFO_URL      = https://rest.bullhornstaffing.com/rest-services/loginInfo
  -> returns JSON: { "oauthUrl": "...", "restUrl": "..." }
  -> auth_endpoint   = {oauthUrl}/authorize
  -> token_endpoint  = {oauthUrl}/token
  -> rest_login_url  = {restUrl}/login

# Bullhorn One (fixed; only if BULLHORN_USE_NEW_API=true) — these are account/data-center specific:
BULLHORN_ONE_AUTH_URL       = https://auth-east.bullhornstaffing.com/oauth/authorize
BULLHORN_ONE_TOKEN_URL      = https://auth-east.bullhornstaffing.com/oauth/token
BULLHORN_ONE_REST_LOGIN_URL = https://rest-east.bullhornstaffing.com/rest-services/login
BULLHORN_ONE_REST_URL       = https://rest45.bullhornstaffing.com/rest-services/dcc900/   # <-- yours will differ
```

> Always prefer the `restUrl` returned by the REST `login` response over any hardcoded value — Bullhorn can move accounts between data centers.

---

## 4. Step-by-step

### Step 1 — Discover endpoints (legacy)
```
GET https://rest.bullhornstaffing.com/rest-services/loginInfo?username=<USERNAME>
```
Read `oauthUrl` and `restUrl` from the JSON. (Bullhorn One: skip this; use the fixed URLs.)

### Step 2 — Get the authorization code (headless)
```
GET {oauthUrl}/authorize
  ?client_id=<CLIENT_ID>
  &response_type=code
  &redirect_uri=<WHITELISTED_REDIRECT_URI>
  &username=<USERNAME>
  &password=<PASSWORD>
  &action=Login
```
**Do NOT follow redirects** (`allow_redirects=False`). On success you get **HTTP 302**; the `Location` header contains `...?code=<AUTH_CODE>`. URL-decode it. If `Location` contains `error=...` instead, auth failed (bad creds or un-whitelisted redirect URI).

### Step 3 — Exchange code for access token
```
POST {oauthUrl}/token
  Content-Type: application/x-www-form-urlencoded
  grant_type=authorization_code
  &code=<AUTH_CODE>
  &client_id=<CLIENT_ID>
  &client_secret=<CLIENT_SECRET>
  &redirect_uri=<SAME_WHITELISTED_REDIRECT_URI>   # MUST match Step 2 exactly
```
Read `access_token` from the JSON.

### Step 4 — Exchange access token for a REST session
```
POST {restUrl}/login?version=2.0&access_token=<ACCESS_TOKEN>
```
Read from the JSON:
- `BhRestToken` — the session token for all API calls.
- `restUrl` — the **authoritative** base URL (use this, not the discovery value).
- `userId` (or `corporateUserId`) — the corporate user id; needed when creating notes (`commentingPerson`). If absent, query `GET {restUrl}settings/userId?BhRestToken=...` or `GET {restUrl}userInfo?BhRestToken=...`.

### Making API calls
Put `BhRestToken` on every request, prefix the path with the returned `restUrl`:
```
GET {restUrl}search/JobOrder?query=id:[1 TO 999999]&fields=id&count=1&BhRestToken=<BhRestToken>
```

---

## 5. Copy-paste Python reference

```python
import requests
from urllib.parse import unquote

def bullhorn_login(client_id, client_secret, username, password, redirect_uri,
                   use_bullhorn_one=False):
    """Headless Bullhorn OAuth login. Returns (rest_token, base_url, user_id)."""
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})

    # Step 1: endpoints
    if use_bullhorn_one:
        oauth = "https://auth-east.bullhornstaffing.com/oauth"
        auth_endpoint  = f"{oauth}/authorize"
        token_endpoint = f"{oauth}/token"
        rest_login_url = "https://rest-east.bullhornstaffing.com/rest-services/login"
    else:
        info = s.get("https://rest.bullhornstaffing.com/rest-services/loginInfo",
                     params={"username": username}, timeout=30).json()
        oauth_url, rest_url = info["oauthUrl"], info["restUrl"]
        auth_endpoint  = f"{oauth_url}/authorize"
        token_endpoint = f"{oauth_url}/token"
        rest_login_url = f"{rest_url}/login"

    # Step 2: authorization code (headless; do not follow redirects)
    auth_resp = s.get(auth_endpoint, params={
        "client_id": client_id, "response_type": "code",
        "redirect_uri": redirect_uri, "username": username,
        "password": password, "action": "Login",
    }, allow_redirects=False, timeout=30)
    location = auth_resp.headers.get("Location", "")
    if "code=" not in location:
        raise RuntimeError(f"No auth code (status={auth_resp.status_code}, loc={location[:200]})")
    code = unquote(location.split("code=")[1].split("&")[0])

    # Step 3: access token
    tok = s.post(token_endpoint, data={
        "grant_type": "authorization_code", "code": code,
        "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }, headers={"Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json"}, timeout=30)
    tok.raise_for_status()
    access_token = tok.json()["access_token"]

    # Step 4: REST session
    rest = s.post(rest_login_url,
                  params={"version": "2.0", "access_token": access_token}, timeout=30)
    rest.raise_for_status()
    data = rest.json()
    rest_token = data["BhRestToken"]
    base_url   = data["restUrl"]                       # authoritative; always use this
    user_id    = data.get("userId") or data.get("corporateUserId")
    return rest_token, base_url, user_id


# Example call after login:
# r = requests.get(f"{base_url}search/JobOrder",
#                  params={"query": "id:[1 TO 999999]", "fields": "id",
#                          "count": 1, "BhRestToken": rest_token}, timeout=15)
```

---

## 6. Gotchas (read these — they cost hours)

1. **Redirect URI must be whitelisted AND identical** in Steps 2 and 3. Mismatch or un-whitelisted = OAuth error, not a code. Get your project's URL whitelisted by Bullhorn Support.
2. **Headless = don't follow redirects.** The auth code lives in the 302 `Location` header. If you let `requests` follow it, you lose the code (and may hit your own callback route).
3. **HTML response = auth failure.** If any step returns `text/html` (a login page) instead of JSON, the credentials/redirect are wrong. Treat HTML as a hard error; don't try to parse it.
4. **Shared API user.** A single Bullhorn API user (username+password) is often shared across multiple apps/environments (prod, dev, other projects). Rotating its password breaks **every** app at once — coordinate before changing it. In this project the live password is the DB value (`global_settings`), which can differ from any stored env secret.
5. **Account lockout.** Too many failed/rapid auth attempts can lock the API user (this happened in prod). Back off on failure (this project rate-limits re-auth to ~once per 5s and reuses an existing valid session instead of re-logging in).
6. **Reuse the session.** Once you have a valid `BhRestToken` + `restUrl`, keep using them; don't re-login on every call. Re-authenticate only when a call returns 401 or the token expires.
7. **Use the `restUrl` from Step 4**, not a hardcoded one — Bullhorn can move accounts between data centers, and Bullhorn One suffixes (`dcc900`, `rest45`) are account-specific.
8. **Notes need `userId`.** If Step 4 doesn't return one, fetch it from `settings/userId` or `userInfo` before creating notes.

---

## 7. Quick checklist for the other agent
- [ ] Obtain `client_id`, `client_secret`, `username`, `password` from Bullhorn Support / account owner.
- [ ] Get your project's redirect URI whitelisted by Bullhorn Support.
- [ ] Decide Legacy (default) vs Bullhorn One; if Bullhorn One, get your fixed endpoints from Support.
- [ ] Store the 4 credentials in a secret store / DB (never hardcoded).
- [ ] Run the Step 1–4 flow; confirm you get a `BhRestToken` + `restUrl`.
- [ ] Make one test call (`search/JobOrder?count=1`) to confirm 200.
- [ ] Add back-off on auth failure to avoid locking the shared API user.
