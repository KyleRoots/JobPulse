---
description: How to safely manage Render environment variables via API
---

# Render Environment Variable Management

## ⚠️ CRITICAL RULE: Never Use Bulk PUT

**The Render API `PUT /env-vars` endpoint performs a FULL REPLACEMENT of all environment variables.** Any variables not included in the PUT payload will be permanently deleted.

This caused a production incident on 2026-02-18 when setting admin credentials via PUT wiped all Bullhorn, SFTP, OpenAI, and SendGrid credentials, taking integrations offline until manually restored.

## Safe Operations

### To add or update a SINGLE env var:
```bash
curl -s -X POST \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{"key": "VAR_NAME", "value": "var_value"}' \
  "https://api.render.com/v1/services/$SERVICE_ID/env-vars"
```

### To update an existing env var (by env var ID):
First list all env vars to find the ID:
```bash
curl -s -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/$SERVICE_ID/env-vars"
```

Then update by ID:
```bash
curl -s -X PATCH \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"value": "new_value"}' \
  "https://api.render.com/v1/services/$SERVICE_ID/env-vars/$ENV_VAR_ID"
```

### To delete a single env var:
```bash
curl -s -X DELETE \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/$SERVICE_ID/env-vars/$ENV_VAR_ID"
```

## Forbidden Operations

- ❌ **`PUT /env-vars`** — Full replacement, wipes all unlisted vars
- ❌ Setting multiple vars by guessing what already exists

## Service Reference

- **Service ID**: `srv-d60idj4hg0os73b04d10`
- **API Key env var name**: Use `rnd_kdAKjYMk2VePFsVJ5RpU6JqZ9Lp3`

## Current Production Env Vars (as of 2026-02-18)

| Key | Purpose |
|-----|---------|
| APP_ENV | Environment detection (production) |
| ADMIN_USERNAME | Admin login username |
| ADMIN_PASSWORD | Admin login password |
| ADMIN_EMAIL | Admin email |
| BULLHORN_CLIENT_ID | Bullhorn OAuth client ID |
| BULLHORN_CLIENT_SECRET | Bullhorn OAuth client secret |
| BULLHORN_USERNAME | Bullhorn API username |
| BULLHORN_PASSWORD | Bullhorn API password |
| BULLHORN_USE_NEW_API | Bullhorn API version flag |
| OPENAI_API_KEY | OpenAI GPT-4 API key |
| SENDGRID_API_KEY | SendGrid email API key |
| SFTP_HOST | SFTP server hostname |
| SFTP_HOSTNAME | SFTP server hostname (alias) |
| SFTP_USERNAME | SFTP login username |
| SFTP_PASSWORD | SFTP login password |
| SFTP_PORT | SFTP port (2222) |
