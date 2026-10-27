# Initial three-brand overview database seed

Client date: 2026-10-26, America/Chicago. Final verification: 2026-10-27T01:33:36.467Z. The user's later instruction explicitly authorized importing the three existing generated local overviews into the existing AI insight database so a later deployment can reuse them. This was a bounded database initialization, not a website deployment or scheduling installation.

## Scope and result

- Selected the existing frontend `KLAVIYO_DATABASE_URL` connection; no connection-string, password, token or private key was printed or copied into this record.
- First performed a read-only preflight: `public.ai_insight_cache` existed with two rows; `public.dashboard_overview_publications` did not exist. Captured the original insight content signature before any write.
- Used the reviewed frontend CLI `scripts/import-dashboard-publications.ts --write --apply-migration` from the frontend repository root. It validates the three existing local frozen publications before connecting, uses one transaction for new-table DDL, three parameterized upserts and per-row canonical SHA-256 readback, and commits only when all hashes match. Its local SQL migration is `sql/dashboard-overview-publications.sql` in the frontend repository.
- The new table contains exactly three rows, keyed by project. Each row is the latest generated publication for that brand, not a growing history of weekly generated reports. Future successful upserts replace that brand's previous generated overview atomically. Dated raw marketing source caches remain available for PoP/YoY and audit.
- Enabled row-level security on **only the newly created table** in a separate bounded transaction after the initial seed. Verified RLS is enabled and the table has zero browser policies; the existing backend database owner can still read all three rows. No other table's policies or privileges were changed.
- A final independent read-only transaction verified every stored payload matches its local frozen publication and `ai_insight_cache` still contains two rows with the identical preflight content signature. No existing insight, reporting fact, dimension or marketing source rows were modified.
- No local custom schedule or Codex automation was created. Future remote-only scheduling remains a template: Tuesday 04:00 America/New_York, bounded retries 05:00–09:00, idempotent once published. No remote timer or website code was deployed.

## Stored overview verification

All three periods are October 13–19, 2026, the most recently completed Tuesday–Monday for the client date.

| Project | Rows in latest-publication table | Range | Canonical payload SHA-256 |
|---|---:|---|---|
| Bluevua | 1 | 2026-10-13 – 2026-10-19 | `c8dd7c785d4f6025c3d4b7fb274e10c1adb13695c1f1fe55daf67c197c59ea4c` |
| Biceek | 1 | 2026-10-13 – 2026-10-19 | `a511209db2d9ab9931704b19243e5d4a3a3359f77d21ecc7d03204e455ce9e79` |
| RingConn | 1 | 2026-10-13 – 2026-10-19 | `ef074dc9d4cdd6a9afd1bb6d8110dfa9f7663def35fa2eb057d9f173c5114083` |

Private local verification artifacts are `/tmp/builder-publication-db-baseline.json`, `/tmp/builder-publication-db-import.json` and `/tmp/builder-publication-db-verification.json`. They contain only counts, project/range identifiers, hashes and validation metadata, with mode 0600. The generated payloads themselves remain in the private publication files and the authorized database table.

## Strict TLS correction

The first import attempt stopped during the TLS handshake with `SELF_SIGNED_CERT_IN_CHAIN`, before SQL execution. A read-only recheck confirmed the target table was still absent and the original insight signature unchanged. No write was blindly repeated.

Supabase's public documentation instructs clients to trust the project CA while retaining full certificate/hostname verification. The certificate download URL was verified against Supabase's own Studio source: `SSLConfiguration.tsx` chooses the production environment, and its `custom-content.json` defines the certificate URL template. The resulting public CA was downloaded over verified HTTPS from the vendor's referenced production S3 location. No certificate validation was disabled for the successful strict-TLS import.

- Official Studio component: https://github.com/supabase/supabase/blob/master/apps/studio/components/interfaces/Settings/Database/SSLConfiguration.tsx
- Official default certificate URL definition: https://github.com/supabase/supabase/blob/master/apps/studio/hooks/custom-content/custom-content.json
- Public CA URL: https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt
- Official TLS guidance: https://supabase.com/docs/guides/platform/ssl-enforcement
- Certificate subject/issuer: Supabase Root 2021 CA, Supabase Inc.
- Certificate validity: 2021-04-28 through 2031-04-26.
- Certificate DER SHA-256: `807025AD50D4ED219D2C9C7D299C004F824EB00CF7F65AFEF607D07B72E6CAFA`.
- Local application CA file: frontend `.state/ai-dashboard/certs/supabase-prod-ca-2021.crt`; this is a public CA certificate, not a credential. The publication provider receives its absolute path through `AI_DASHBOARD_PUBLICATION_DATABASE_CA_FILE` and keeps `rejectUnauthorized:true`.

The collector/seed worker did not alter the local publication-provider environment. Any later explicit database-backed preview selection is separate from this initialization and does not install a scheduler.
