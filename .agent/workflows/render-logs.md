---
description: How to access Render production logs via API for debugging and monitoring
---
// turbo-all

# Render Log Access via API

Use the Render API to fetch production logs directly, without needing to log into the Render dashboard.

## Credentials

The Render API key and service ID are stored in `.env`:

```
RENDER_API_KEY=rnd_kdAKjYMk2VePFsVJ5RpU6JqZ9Lp3
RENDER_SERVICE_ID=srv-d60idj4hg0os73b04d10
RENDER_OWNER_ID=tea-d60hvnkoud1c7385gcg0
```

## Fetch Recent Logs

```bash
curl -s \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Accept: application/json" \
  "https://api.render.com/v1/logs?ownerId=tea-d60hvnkoud1c7385gcg0&resource=srv-d60idj4hg0os73b04d10&limit=500&direction=backward"
```

## Filter Logs by Keyword

Pipe through Python to filter for specific terms:

```bash
curl -s \
  -H "Authorization: Bearer rnd_kdAKjYMk2VePFsVJ5RpU6JqZ9Lp3" \
  -H "Accept: application/json" \
  "https://api.render.com/v1/logs?ownerId=tea-d60hvnkoud1c7385gcg0&resource=srv-d60idj4hg0os73b04d10&limit=500&direction=backward" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
logs = data if isinstance(data, list) else data.get('logs', [data])
keywords = ['YOUR_KEYWORD_HERE']  # Replace with search terms
for entry in logs:
    msg = entry.get('message', '') or entry.get('text', '') or str(entry)
    for kw in keywords:
        if kw.lower() in msg.lower():
            ts = entry.get('timestamp', '')[:19]
            print(f'[{ts}] {msg[:350]}')
            break
"
```

## Filter by Time Range

Add `startTime` parameter (ISO 8601 format):

```bash
&startTime=2026-02-15T00:00:00Z
```

## Common Keywords for Debugging

| Area | Keywords |
|------|----------|
| Vetting | `vetting cycle`, `note`, `Scout Screen`, `create_candidate_note` |
| Email parsing | `email_inbound`, `ParsedEmail`, `AI Resume Summary` |
| Errors | `ERROR`, `failed`, `exception`, `500` |
| Bullhorn | `authenticate`, `BhRestToken`, `bullhorn` |
| Scheduler | `apscheduler`, `executed successfully` |

## Reference

The existing `log_monitoring_service.py` uses the same API for automated monitoring. See `LogMonitoringService.fetch_logs()` for the canonical implementation.
