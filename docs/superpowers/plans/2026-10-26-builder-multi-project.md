# Dashboard Builder Multi-project Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or executing-plans task-by-task.

**Goal:** Locally connect Bluevua, Biceek, RingConn examples using actual read-only data, complete Tue–Mon weeks, a captioned silent tutorial, and an always-visible desktop composer.

**Architecture:** Trusted project execution context scopes all read-only queries, jobs, MCP and caches. Native source responses are normalized into project-specific private caches; previews/availability depend on these real caches. Client examples share the editor UI but have isolated saved state. Shared account header remains unchanged.

**Tech Stack:** Existing Next.js/React/BoardUI, Python adapters, ANC read-only CLI over SSH, CSS/Motion and live-DOM PDF.

- [ ] Data adapter: fixed-brand/source/account allowlists; import exact-date evidence first, query only missing current/PoP/YoY periods; retain full response coverage/currency/timezone and unavailable vs empty semantics; no credentials copied locally.
- [ ] Backend: trusted project scope through API/job/MCP/materialization; GA4/native totals and Shopify cache normalization; independent project availability and initial reports; no cross-brand fallback.
- [ ] Complete-week rule: recent fully ended Tue–Mon in America/Chicago; Monday 2026-10-26 => Oct13–19; Tuesday Oct27 => Oct20–26. Tests cover boundaries and year rollover.
- [ ] Client: query-string project/example entry; actual Data ready green label/dot; current examples use corrected weekly range while preserving user custom text/colors/layout.
- [ ] Landing: three branded examples and readable captioned silent operation animation with playback control and reduced-motion handling.
- [ ] Composer: measure actual shared account header viewport occlusion, constrain sticky chat height to remaining visible viewport, min-height:0 messages scroll region, composer shrink:0; messages scroll internally instead of scrolling page.
- [ ] Verify read isolation/default periods and regression tests, actual data coverage, browser project navigation and chat scroll at top/bottom, tutorial controls, white PDF excluding controls, TypeScript/eslint. Local only, no deployment.
