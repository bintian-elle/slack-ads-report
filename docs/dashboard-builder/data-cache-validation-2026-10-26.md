# Three-project local data cache validation

Validated for the client date 2026-10-26, America/Chicago. Last Week means the most recently **completed** Tuesday–Monday: 2026-10-13 through 2026-10-19. It advances to October 20–26 only on Tuesday October 27. PoP is October 6–12; YoY is October 13–19, 2025. ANC command end dates are exclusive.

## Adapter and storage

- Reusable adapter: `scripts/cache-builder-projects.py` (Python standard library only).
- Commands: `python3 scripts/cache-builder-projects.py --project biceek`, `--project ringconn`, or `--project all`.
- Optional trusted environment: `DASHBOARD_ROOT` and `AI_DASHBOARD_RANGES` (JSON inclusive start/end pairs, at most three periods). `--force` explicitly rereads approved sources. Default dates use America/Chicago and the completed Tuesday–Monday rule.
- The adapter invokes only fixed, whitelisted ANC read-only CLI report operations through the existing `EMW3_ANC_AI` SSH alias. It does not start a worker, timer, daily report sender, or deployment and does not submit marketing mutations.
- Credentials stay on ANC. Only aggregate analytics/report data is requested; no customer, profile or order-object exports.
- Private normalized cache: frontend `.state/ai-dashboard/projects/<project>/overview/<source>-<start>-<end>.json`. Aggregate source evidence is under the sibling `raw/` directory. Files are mode 0600 and private directories are created with mode 0700; caches remain outside Git.
- Cache identity is explicit: `project`, `source`, `accountRef`, `accountId`, `identityVerified`, live `currency`, live `timezone`, range and coverage. Identity must match the server-side project/account whitelist.
- A trusted CLI `export_file` is expanded only inside the ANC data tool's private exports root, with JSON/JSONL metadata and row counts checked. A preview is never treated as complete data.

## Actual reads for October 13–19

| Project / source | Verified result | Caveat |
|---|---|---|
| Biceek GA4 `497540678` | 7 daily rows, native totals: 3,198 sessions, 10 ecommerce purchases, $761.089874 GA4 purchase revenue | Native period sessions differ from daily-session sums; the overview uses the native totals |
| Biceek Shopify COM `81924522263` | 7 daily rows, 18 sales-dataset orders, $1,398.15 total_sales | ShopifyQL sales dataset definition; separate from GA4/advertising attribution |
| Biceek Klaviyo `THABgs` | Complete period message report: $119.01 attributed revenue, 2 conversions, 433 delivered, 39 per-message unique clicks, 204 per-message unique opens | Period totals and channel/message breakdowns only; no invented daily trend; unique message counts are not deduplicated people |
| Biceek Google Ads `3508380986` | Identity verified, account query complete but no reporting rows | Stored unavailable; no invented $0 spend |
| Biceek Meta `4415468505361063` | Live read returned access_denied, HTTP 400 / Meta code 200 before identity verification | Prior acceptance record does not prove current permission; stored unavailable, no token changes or alternative-brand fallback |
| RingConn main GA4 `377025799` | 7 daily rows, native totals: 328,027 sessions, 1,235 purchases, $351,291.92 GA4 purchase revenue | All reads filter hostName to ringconn.com / www.ringconn.com; other four properties are not added |

All successful current sources report USD and America/Los_Angeles. The rejected Meta response retains its registered America/New_York context but is not marked identity-verified or ready. RingConn's historical timezone-change effective date remains unverified; original source metadata is retained rather than silently rewritten.

## Comparison coverage

- Biceek GA4: current, PoP and YoY all have complete daily data, native period totals and native channel breakdowns.
- Biceek Shopify: PoP returned 2 orders / $111.58; YoY explicitly returned seven daily zero-value rows. These verified zero rows are retained; YoY percentage remains unavailable when the baseline is zero. Initial busy responses received one bounded retry through the same CLI and then succeeded; no lock was removed or bypassed.
- Biceek Klaviyo: current report is ready. PoP and YoY return no message reporting rows and are unavailable, not zero-filled.
- Biceek Google Ads and Meta retain their unavailable state for all three periods.
- RingConn native totals: PoP 332,925 sessions / 1,478 purchases / $391,437.42; YoY 104,133 sessions / 1,128 purchases / $290,249.04. Each period also has seven daily rows and native channel breakdowns.
- Bluevua: reused existing October 13–19 caches; the existing read-only database exporter and account adapters filled October 6–12 and October 13–19, 2025. Existing missing TikTok/Reddit/ChatGPT historical data remains missing; aggregate paid YoY cannot compare different channel coverage.

`totals` is authoritative only for the exact cached range. GA4 sessions/users are not reconstructed by summing daily rows. Klaviyo supplies exact period totals and `breakdowns.channel` / `breakdowns.campaign`, with an empty `rows` array; this does not mean zero revenue. Failed/partial/empty/unavailable values remain distinct. Store, GA4, advertising and email revenue must never be added to produce a combined revenue total.

## Verification

`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_builder_projects tests.test_builder_tiktok` passed 10 tests. They cover completed-week boundaries, native GA4 totals, account mismatch rejection, missing versus explicit zero values, Shopify sales normalization, incomplete Klaviyo reports and bounded query ranges. Actual CLI responses supplied the fixture data above; tests do not fabricate the online coverage claim. No online deployment was performed.
