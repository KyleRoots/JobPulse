---
name: Recruiter notification email surfaces
description: screening/notification.py has THREE independent recruiter-email HTML builders that do NOT share a template
---

`screening/notification.py` builds recruiter candidate-alert emails in **three
separate places**, each with its own hand-written `html_content` f-string (they
do NOT reuse a common template):

1. `_send_recruiter_email` — qualified-candidate alert (the main one).
2. `_send_prestige_review_notification` — below-threshold candidate at a prestige firm.
3. `_send_location_review_notification` — strong tech fit knocked below threshold by location penalty.

**How to apply:** any change to what recruiters see in candidate emails (new
badge, banner, disclaimer) must be applied to all three, or it silently appears
on only one path. Prefer a small reusable helper that returns an HTML fragment
(e.g. `_build_fraud_banner_html(candidate_id)`) and inject it into each builder
right after the `👤 candidate` card / before the per-job section header — rather
than copy-pasting markup three times.
