# Live KOL metrics specification

User request: every five-minute Slack poll must insert new post identities AND
immediately attempt all available metrics in every currently automated tab:
Partnership Ads Meta/TikTok and Organic Meta-IG/TikTok/YTB. Daily full refresh
continues at 02:00 America/New_York. Do not affect the Robot.

Exact post identity/code/video ID deduplication must preserve manual rows, fees,
special statuses, formulas, notes and native dropdowns. Never associate platforms
or creators by proximity alone. A missing YouTube link is not permission to use
another creator's link. Tracking links supplied by Slack may be resolved only
through HTTPS allowlisted hosts to verified official post URLs.

New-row metrics must not wait for the daily run. Failures leave the identity
row and an English reason and enter a durable targeted retry queue. Successful
rows are not queried every poll. API-unavailable metrics are never fabricated.
TikTok public reads retain 3–7 second spacing, daily successful-result cache and
403/429 backoff. Queries/writes target new/pending identities; full ad inventory
reads may remain necessary for exact binding. Conflicts across all rows still
block shared-ad attribution.

Organic Meta uses public lifetime Views/counters, existing Method A Interaction
and CPE, with no basis labels. Current manual nutrition row must not duplicate.
