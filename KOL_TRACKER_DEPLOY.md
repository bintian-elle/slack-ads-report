# 独立 KOL Tracker 部署

在原项目目录运行，共用现有 `.env` 和凭证。生产唯一入口 `kol_tracker.py`，
不启动、不导入、不重启原来的 app。依赖安装到 `.venv-kol`，不改原虚拟环境。
Python 3.10+、Linux systemd、可使用 sudo 的部署用户。

## 首次安装（EC2）

```bash
cd /home/ubuntu/slack-ads-report
git pull --ff-only origin main
python3 -m venv .venv-kol
.venv-kol/bin/python -m pip install -r requirements-kol-tracker.txt
```

无需复制配置，直接使用原目录的 `.env` 和 Google service account JSON。
不要修改旧程序的 token、虚拟环境或服务。
`.env` 必需键：

- `KOL_TRACKER_GOOGLE_SHEETS_LINK`
- `KOL_TRACKER_SLACK_CHANNEL_ID`
- `KOL_TRACKER_SLACK_BOT_TOKEN`（history + replies 读取权限，bot 已加入频道）
- `META_ACCESS_TOKEN`、`META_AD_ACCOUNT_ID`
- `TIKTOK_ACCESS_TOKEN`、`TIKTOK_ADVERTISER_IDS`（逗号分隔或 JSON 列表）
- `GOOGLE_SERVICE_ACCOUNT_FILE`（默认 `credentials/google-service-account.json`）

可选 `KOL_TRACKER_TIKTOK_ACCESS_TOKEN` 优先于已有 TikTok Token。
状态默认写到专用目录 `data/processed/kol_tracker_runtime`，可设置
`KOL_TRACKER_STATE_DIR` 为独立持久目录。不要放在旧程序共享输出位置。
Google service account 需要此原生 Sheet 的编辑权限。

```bash
.venv-kol/bin/python kol_tracker.py check
.venv-kol/bin/python kol_tracker.py poll
.venv-kol/bin/python kol_tracker.py daily
```

以上都是只读预览（私有状态/备份会写本地，Slack 游标不推进）。核对日志后启用：

```bash
.venv-kol/bin/python kol_tracker.py poll --apply
.venv-kol/bin/python kol_tracker.py daily --apply
bash deploy/install_kol_tracker.sh
```

安装器只新增/更新 `kol-tracker-*` 四个 systemd unit。Slack 每 5 分钟；广告
每天美国东部时间 `America/New_York` 02:00，自动适应夏令时。`Persistent=true` 允许宕机后补跑；春季跳时当天 02:00 不存在时应检查 timer 的下一次触发。
共用文件锁防止 Slack 和每日任务同时修改表格。轮询遇到占用则跳过，每日任务等待锁释放。

## 后续更新

```bash
cd /home/ubuntu/slack-ads-report
sudo systemctl stop kol-tracker-poll.timer kol-tracker-daily.timer
# 等当前服务完成，避免运行中更换代码：
while systemctl is-active --quiet kol-tracker-poll.service || systemctl is-active --quiet kol-tracker-daily.service; do
  sleep 5
done
git pull --ff-only origin main
.venv-kol/bin/python -m pip install -r requirements-kol-tracker.txt
.venv-kol/bin/python -m unittest discover -s tests
.venv-kol/bin/python kol_tracker.py check
bash deploy/install_kol_tracker.sh
```

若服务仍 active，等待其完成后再 pull，不要杀死正在写 Sheet 的任务。
不要执行任何旧 app 服务的 restart。无需创建新 checkout。

## 日志／停用

```bash
systemctl list-timers 'kol-tracker-*'
journalctl -u kol-tracker-poll.service -n 80 --no-pager
journalctl -u kol-tracker-daily.service -n 80 --no-pager
sudo systemctl disable --now kol-tracker-poll.timer kol-tracker-daily.timer
```

失败时保留备份，先检查日志，不能盲目重放写入。不要删除持久状态目录：里面
有 Slack 游标、公告线程缓存和备份。运行文件含业务信息，勿提交 GitHub。

## 行为与边界

- 首次扫描最近 90 天；之后从游标回看一天，并复查保留期内已知 KOL 公告线程。
  超过 90 天的旧线程回复不覆盖；可修改 `KOL_TRACKER_SLACK_RETENTION_DAYS`。
  多线程读取可能超过五分钟；systemd 不重叠运行同一服务，429 有限重试，失败不推进游标。
- 只有 `[KOL Content is Live]` 公告及其明确关联的回复可新增；需要完整
  Creator、Code、对应平台 Post Link。冲突内容记到私有报告，不猜测归属。
- Code／Link 去重。拿不到广告匹配也先插入；Launch Date 使用芝加哥公告日。
  Meta 精确匹配后新行使用最早广告创建日，并初始化 testing/paused 和有效位置。
  TikTok 新行 F 暂空，已有人工 Status 永不改动。
- 按 Launch Date 插入到相应位置，保留底部预置 briefs、原生格式、验证和 N 的 ID。
- Meta 每日 J:S 使用广告账户时区前一日截止的 Insights；只精确 Code 匹配。
- TikTok 每日 H:M 使用当前请求时刻的 lifetime 报表，需要 `/ad/get/` 读取权限。
  完整 Post Link 优先通过视频编号与广告 `tiktok_item_id` 精确匹配；与 N
  绑定矛盾或广告集合不同则保留原行并报告冲突，不擅自扩大统计范围。
  没 ID 的历史 paused 行仅报告精确候选，不自动更新。完整视频编号没有命中
  时不退回名字猜测。其他无编号链接仍可在名字／帖子用户名及日期窗口（-1 至
  +14 天）归为同一广告名称组且没有跨行争用时绑定，并写入 N。这是名称交叉
  核验而非精确 Ad Code→Ad ID 证明，仍可能因广告命名错误而误判。
- 多广告组 Spend、Purchase、Views、Hook Rate 汇总；Reach、ROAS 保留，直到
  有可靠 API 去重 Reach／收入汇总。不把未匹配或 API 失败写成零。
- 不依赖任何本机历史核验 JSON；凭证和表内 N 的绑定足够启动生产入口。
- EC2 安装和 systemd 运行验证由用户执行；本机只读 API 预览不能证明 EC2 权限。

## Ad Code 查询核验（2026-10-05，尚未部署）

TikTok 官方 Postman 集合列出只读 `GET /open_api/v1.3/tt_video/info/`，
参数为 `advertiser_id` 和 `auth_code`（此处是素材 Spark Ad Code，非 OAuth
登录授权码）。这是素材查询，不直接返回 Spend；仍需帖子 ID 对应 Ad ID 后读报表。
同集合的 `POST /tt_video/authorize/` 是应用素材授权的写操作，本次未调用。

新 Token 的 `/ad/get/` 与 Reporting 均返回 code 0，但以三个表内 Code
测试 `/tt_video/info/` 均返回 HTTP 200、code 40001：
`advertiser does not grant you /tt_video/info/:GET permission`。
因此当前阻碍是该独立接口权限，不能推断 Code 无效或官方不支持 Code 查询。
素材 Code 路径本次不接入定时任务，不额外申请权限、不导入素材。

来源：https://www.postman.com/tiktok/tiktok-api-for-business/documentation/efqhadc/tiktok-business-api-v1-3
# Organic Meta metrics (public lifetime totals)

When `KOL_ORGANIC_SHEETS_LINK` is configured, the existing daily KOL task also
updates its `Meta-IG` tab at America/New_York 02:00.
Uses the shared `META_ACCESS_TOKEN` with `business_management`,
`instagram_basic`, and `instagram_branded_content_ads_brand`, plus access to
the configured brand assets. Optional IDs: `KOL_ORGANIC_META_BUSINESS_ID`
(default `997763325183322`) and `KOL_ORGANIC_META_IG_USER_ID`
(default `17841448894150543`). Use a production-valid token; Explorer short-lived
tokens can expire before the next daily run. `KOL_ORGANIC_META_ACCESS_TOKEN`
is no longer used; keep the shared token valid for both Ads and Organic.

Partnership Ads Meta/TikTok daily writers locate the existing
`Reason for data update failure` header rather than assuming a fixed column.
Row-level failures (no exact binding, duplicate ad claims, no Insights,
invalid metrics) retain metrics and write English reasons. Partial TikTok
ROAS/Reach preservation is also explained. Managed `Update failed: ` notes
clear after complete recovery; manual notes are preserved. No column is
created if the header is absent. Account-level API failures still abort
before writes rather than assigning guessed per-row failure causes.

### Organic YouTube (YTB)

Set `YOUTUBE_API_KEY` alongside `KOL_ORGANIC_SHEETS_LINK` in the existing `.env`.
The five-minute poll includes YouTube `KOL Content is Live` announcements and
explicit ordinary launch notices (for example, "contents have been published").
Thread replies are included, including later updates inside old contract threads.
Creator must be unambiguous in Slack; videos are deduplicated by exact video ID.
Only videos with an API publication date in 2026 are inserted, before Summary;
existing rows are not reordered. New rows copy native formats/validation, use
the API publication date (Eastern), and leave fees blank for manual maintenance.
Summary sums expand to cover inserted rows. No creator discovery from search.

Daily 02:00 Eastern updates public lifetime Views, Likes, Comments and the
derived Interaction (Likes + Comments), CPM and CPE. Public Views are total
YouTube views, not a guaranteed organic-only count. Shares/Saves are neither
queried nor written nor treated as errors. Existing formulas/manual notes are
preserved. Old automatic Shares/Saves failure explanations are cleared.
The actual failure-reason column is resolved by its English header.

Standalone preview/write commands (same task lock as the other KOL jobs):

```bash
.venv-kol/bin/python kol_tracker.py youtube-poll
.venv-kol/bin/python kol_tracker.py youtube-poll --apply
.venv-kol/bin/python kol_tracker.py organic-youtube --apply
```

Preserve `organic-youtube-slack-state.json` during deployment. It checkpoints
thread metadata and discovered candidates; unchanged threads are not re-read.
Private before/after snapshots and plans are saved under the runtime runs directory.
The existing Robot service is not modified or restarted.

Organic TikTok public counters are included in the same daily 02:00 Eastern
job. Standalone preview: `.venv-kol/bin/python kol_tracker.py organic-tiktok`;
write: `.venv-kol/bin/python kol_tracker.py organic-tiktok --apply`.
This uses the public page parser in `get_tiktok_public_data.py`, not Ads API
metrics. Video IDs are deduplicated; requests have a 3–7 second interval and
at most one attempt per video per run. Successful data is reused for the
Chicago calendar day; a failed attempt without usable data can retry on a
later manual run. HTTP 403/429 backoff is never bypassed. Preview caches
successful results for a later apply. Retain
`organic-tiktok-state.json` in the configured state directory across deploys.
HTTP 403/429 stops all remaining public requests without retry, persisting
at least 24-hour backoff and honoring any longer Retry-After. Redirects are
not followed. Successful rows collected before a block and English failure
reasons can still be written; the task reports the restriction as a failure.
Missing/invalid counters retain existing sheet values and do not stop later
videos. The final populated column is followed by `Reason for data update
failure`; an existing column with that header is reused. Reasons are English,
without an automatic prefix; recovered rows clear managed reasons, while
manual notes are preserved. Backoff runs issue no public requests and can
record that reason. Views date advances only when metrics are written.
E:J receive public lifetime Views, four-part Interaction, Likes, Comments,
Saves and Shares. F Interaction is computed as likes + comments + saves + shares
only when it has no formula; existing F formulas are preserved and recalculate
from refreshed G:J. K:L use manual D fee for CPM/CPE; their formulas remain.
E1 shows `Views` followed by `[MM/DD update]` on a new line, indicating the
latest successful batch, not that every video succeeded. Status is untouched.

`kol_organic_meta.py` queries Partnership Ads Content Discovery by exact post
permalink after a complete cursor-based discovery pass. Missing Reels from
`KOL_ORGANIC_META_YEAR` (default 2026) are inserted only when that exact post's
`collaborators` response includes the Bluevua IG ID with `invite_status=Accepted`.
Author names or previous collaborations do not establish eligibility. Empty,
pending, incomplete or failed responses are excluded and recorded privately in
`Organic-Meta-discovery.json`.
Posts are deduplicated by shortcode and content ID. New rows copy only safe
formatting and validation, not fees, manual status, codes or notes. Existing
rows retain their order and values; new rows are inserted into B-column native
date order (after existing same-date rows). Invalid or unsorted existing dates
block insertion. Dropdowns are copied natively; fees remain blank for manual entry. Insertion is verified
before the separate metrics stage; if metrics later fail, inserted identities
remain and the next run does not duplicate them.

The production entry point is `kol_organic_meta_totals.py`. It verifies both
returned Media ID and permalink against the exact sheet post, reads H only from
Business Discovery `view_count`, and reads the original Advertisable Content API's
`organic_insights.likes`, `comments`, `shares`, `saves` into J:M independently.
Views queries do not depend on root `?ids=` metadata (deprecated in Graph v26).
Only failed exact-post lookups may use a single Media GET to recover a username
alias; successful Views are never discarded because metadata is unavailable.
Creator pagination stops after finding the requested posts or passing the configured
publication year. A rate-limit response or 80% app usage defers remaining Views,
retaining existing values and recording the actual failure reason.
To refresh only Views, CPM and their managed failure notes without fetching or
changing other metrics, run:

```bash
.venv-kol/bin/python -u kol_tracker.py organic-views --apply
```

Missing Saves are optional and never generate a failure reason. Never substitute
`total_views_count` or natural Views when Business Discovery Views are missing.
Public counters are not guaranteed to reproduce a differently dated Business
Suite export; compare publication-to-current windows and contemporaneous reads.
Never substitute natural Views for public Views. I/CPE deliberately retain the approved Method A:
natural Interaction (four-part fallback only when complete) plus exact-source-media
Instagram paid net likes, net comments and shares. Paid pause caching remains as
implemented by `kol_organic_meta.py`; no public counters are added to Method A.

### Shared KOL Meta budget and continuation

The production `kol_tracker.py` CLI installs a process-local Graph GET guard for
all KOL Meta paths, including inventory, discovery, Views and Ads Insights.
Robot never installs/imports this guard and is not restarted by KOL deployment.
Other hosts/apps sharing Meta credentials remain outside this guard.

- Non-paused ads are queried before paused ads. The first seven days after the
  first observed pause are refreshed daily; thereafter paused advertising
  metrics refresh every seven days. Reactivation resets the pause observation.
  Organic post Views/counters can still grow without ads and keep their daily
  schedule; they are not frozen just because advertising is paused.
- Successful Graph response pages are atomic private checkpoints in
  `meta-request-progress.json`. Metric pages expire at the next Eastern 02:00
  boundary; normal inventory reads refresh after 15 minutes. A queued continuation
  reuses that reporting window's inventory snapshot across cooldowns so it does
  not restart discovery/pagination; the next daily boundary refreshes it. Paid/advertising metric caches
  also track the reporting window and pause state. A retry never publishes a
  partial ad sum or substitutes stale missing data.
- Each invocation sends at most 40 Meta network requests by default
  (`KOL_META_REQUEST_BATCH_SIZE`, bounded to 1–100). Checkpoint hits do not consume
  this budget. A batch limit queues the remainder for the five-minute poll.
- App, ad-account and business-use-case usage headers share an 80% threshold.
  HTTP 429 or Meta codes 4/17/613/80002 also trigger a persisted one-hour cooldown.
  During cooldown no new KOL Graph requests are sent, even after process restart;
  the next attempt checks usage again, not assumes recovery. The successful
  response that reaches the threshold is retained.
- Network/temporary HTTP failures are queued with 30-minute backoff. Stable
  400/401/403/404 lookup responses are cached for six hours to avoid consuming
  every continuation batch on the same inaccessible post. Missing/unsupported
  identities are still reported, not manufactured into successful metrics.
- The existing poll timer resumes only queued Meta stages, not the whole daily
  task or TikTok/YouTube. Progress logs show network calls, checkpoint hits and
  pending stages; private state records the last usage headers and stage.
- Plan-only commands do not persist new continuation jobs or modify the quota
  state. Native Sheets formatting/manual-field guards remain unchanged.

Missing counters retain prior values and Q states
the unavailable metric names, without basis labels; missing Saves is not zero. A known four-part I
formula is frozen at its old result when totals are incomplete, avoiding a mixed
sum. CPM/CPE require fresh nonzero total Views/Interaction respectively.
Public lifetime counters need not equal a date-filtered Business Suite report;
unavailable metrics are not claimed to reproduce the full web report.
IDs are resolved through Content Discovery, exact Creative source-media binding,
manual Reel ID, or Creator Business Discovery with exact permalink verification.
`organic-meta-identities.json` caches identities only, never metric freshness.
F1 advances only after a successful counter write and includes the partial batch
count. No CSV data is used. N:O use the existing manually maintained G fee:
fee/views*1000 and fee/interaction.

The five-minute `poll` now uses `kol_live_updates.py` for all five automated tabs:
Ads Meta/TikTok and Organic Meta-IG/TikTok/YTB. A single Slack snapshot supports
formal and ordinary launch notices, replies and edited parents. Insert identity
rows, then fetch available metrics in the same poll, without waiting for daily.
`live-metric-jobs.json` persists jobs before inserts, completed identities, exact
tracking-link aliases, sanitized failures and retry times. Initial rollout looks
back seven days; later polls overlap one day and inspect changed threads.
Successful jobs do not repeat every five minutes. Unmatched/unavailable jobs retry
after five minutes for the first six attempts, then hourly. TikTok 403/429 and link
resolution restrictions retain 24-hour/minimum Retry-After backoff; successful
TikTok public reads still reuse same-day cache and use 3–7 second request spacing.
Native writes target exact pending posts only; full ad inventory/report reads may
still be required to validate Ad Name bindings. A rejected row is isolated; other
rows/tabs continue. Existing daily 02:00 America/New_York defaults are unchanged.
Deduplicate /p/ and /reel/
by exact shortcode even when the manual Creator cell is blank. API publication
date is preferred, otherwise use the Slack message date in America/New_York.
Copy Creator/link/code; fees remain manual; Q explains missing data. Later Slack
codes fill only blank E on exact post matches; nonblank/manual codes are retained.
Legacy Meta-only identity preview: `kol_tracker.py organic-meta-poll`.
Unified read-only preview: `kol_tracker.py poll`; add `--apply` to insert and fetch.
Focused read-only totals: `kol_tracker.py organic --creators nutritionbyanastasiia eva.hooft`.
Existing N:O formulas are preserved; literal cells are recalculated when inputs
are valid. Null/missing metrics and unmatched posts retain existing values,
with omissions in the private run plan; zero denominators retain derived values.
Manual A:D and G, notes, row order and headers remain unchanged. Existing E
Ad Codes are never overwritten. Blank E cells can be filled only when the exact
Instagram post shortcode uniquely matches Meta Ads Tracker G:H and that Code
also exactly matches a live API Creative. Conflicting post/code identities and
Ad IDs shared by multiple Organic rows are rejected. This is not a direct
Post Link-to-Ad Code API conversion. Organic F
updates only existing `testing`, `pause`/`paused`, or blank cells, using exact unshared
Creative Ad Code bindings, or exact Post Link -> returned content ID -> Creative
`source_instagram_media_id` bindings. A conflicting code/media binding or Ad ID
claimed by multiple rows blocks both automatic status and code writes. Blank E
can also receive a unique `instagram_boost_post_access_token` returned by the
exact source-media Creatives, only when that code does not map to other ads.
No Media ID is written as an Ad Code; existing E is preserved. Any ACTIVE ad
means `testing`; exclusively paused
ads mean `pause` (or the existing dropdown's `paused` spelling). Dropdown options
are never modified. T0 and other manual statuses, unknown statuses, and unmatched
or shared ad bindings remain unchanged. Organic Meta Slack fallback can add
identity-only rows independently of API discovery. Native snapshots,
pre-write change detection and readback verification protect the existing sheet.
`daily` is read-only by default; `daily --apply` is used by the existing timer.
Organic F1 is updated to `[MM/DD update]` in America/New_York time in the same
atomic batch as successful content updates. Failed fetches/writes do not advance
F1; runs without writable data do not advance it. All other headers are preserved.
Progress logs show content batches and the ad status query stage.

Meta-IG Q2 is `Reason for data update failure`. English messages, without a prefix, explain missing/unsupported
post links, posts absent from the API response, partial metrics, missing manual G
fees, and zero/missing CPM/CPE denominators. Blank cells, legacy `自动：` notes,
and recognized generated English reasons are managed; user notes are preserved.
Legacy headers/notes migrate on the next run; reasons clear after recovery.
New manually added content rows receive the same checks on the next daily run;
API and unified Slack discovery support automatic row insertion as described above.
Metric failures retain existing values and explain unavailable fields.
For Organic-only preview: `.venv-kol/bin/python kol_tracker.py organic`.
For Organic-only write: `.venv-kol/bin/python kol_tracker.py organic --apply`.
Both share the existing tracker lock; no extra timer or robot restart is needed.
