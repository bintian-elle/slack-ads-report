# Dashboard Builder：Biceek / RingConn 数据接入调查

调查日期：2026-10-26（America/Chicago）。范围：只读查找现有数据获取方式与本地接入准备；不部署，不修改远程服务，不复制凭证值。

## 本地现状（直接核查）

- 前端仓库：`/Users/yypf/elle-media/dashboard/emw3-dashboard`。
- 本地采集脚本仓库：`/Users/yypf/elle-media/slack-ads-report`。
- `lib/ai-dashboard-options.ts` 中 Bluevua 为 available；Biceek、Ringconn 为 unavailable。不能仅修改此标志来接入新品牌。
- `scripts/cache-builder-channels.py` 当前明确校验 Bluevua 账户，Meta、Microsoft、Reddit、ChatGPT Ads 均存在 Bluevua 归属约束；TikTok 使用 Bluevua reporting context。不得换一个环境变量后绕过这些校验。
- `scripts/export-dashboard-builder-data.cjs` 使用现有 Google Ads / Klaviyo 数据库导出。须确认连接对应的项目、账户过滤与表结构后才能复用到其他品牌。
- 前端 `.state/ai-dashboard/overview/` 当前为扁平源数据缓存，以 source/date 命名；`lib/ai-dashboard-overview.ts` 的读取流程没有独立品牌命名空间。现有 IndexedDB 报表编辑状态虽按项目保存，不能据此认为后端源数据也已隔离。

## 后续实施涉及的本地入口

| 位置（前端仓库内） | 当前行为 | 多项目接入需调整 |
|---|---|---|
| `lib/ai-dashboard-options.ts` | 两个新品牌 unavailable | 数据映射与验证通过后才启用 |
| `app/api/ai-dashboard/catalog/route.ts`、`lib/ai-dashboard-preview.ts` | 统一调用 Bluevua 默认 overview | 按项目构造渠道目录、标题与数据查询 |
| `lib/ai-dashboard.ts` | query/materialize 没有项目数据命名空间 | 把项目绑定到只读查询上下文与 query key |
| `lib/ai-dashboard-overview.ts` | 单一 overview 根目录与渠道集合 | 按品牌/账号读取缓存及覆盖率 |
| `lib/ai-dashboard-refresh.ts` | 统一执行 Bluevua 采集脚本 | 调用对应品牌的只读适配器与缓存目录 |
| `lib/ai-dashboard-jobs.ts`、`scripts/ai-dashboard-worker.ts` | 默认 Bluevua 预览、提示词及 MCP cache 路径 | job 明确记录项目，绑定项目专属 MCP 上下文，校验历史报表归属 |

项目隔离应落在服务端可信上下文；不能让 AI 自行提供数据库、凭证、任意路径或跨品牌账号。

## 接入时必须保持的约束

1. 把 project → source → account/property/store 显式映射列为服务端白名单；确认项目归属后才允许读取。
2. 缓存键至少包含 project、source、account、起止日期与 schema 版本；建议目录 `.state/ai-dashboard/projects/<project>/<source>/`，避免覆盖现有 Bluevua 文件。
3. 从 catalog、job、worker、只读 query 到物化结果均传递并校验 project；AI 输出不能选择任意账户或连接串。
4. 优先复用已验证的 Dashboard/日报读取工具与缓存；缺失期间再用同一账户的官方只读报表 API 获取并缓存。
5. 每个来源保留原始币种、时区、日期边界、归因定义和 account ID。未核实币种不能默认 USD；不同来源的归因收入不能相加为店铺收入。
6. 本期、PoP、YoY 必须使用相同项目与账户集合。缺失对比期保留 null / unavailable，不用其他品牌、其他时段或 0 替代。
7. 凭证只记录远程配置路径、变量名与安全使用方式；不在本文件、Git、AI 上下文或浏览器中保存密钥。

## 远程调查结果

以下为远程 Codex 返回结果的整理稿。验证依据为已有登记、验收与日报证据，不冒称本轮重新调用成功。文中的 `/home/ubuntu/...` 均为 ANC 路径，不是本地 Mac 路径。

**已找到可复用入口：BICEEK 有七类已登记数据源，RingConn 有五个 GA4 属性。优先使用现有 ANC CLI 和日报聚合证据，不应直接复用归属未确认的 Dashboard 数据库。**

远程 Codex 仅只读检查 ANC 上已有文件、账号登记与验收记录；没有新拉取业务 API、发送消息、修改配置或部署。本地文档由本会话整理保存。

**1. 两品牌的数据源与账号**

下表标识来自当前私有账号登记的白名单字段读取；“已接通”依据已有验收记录，不表示本轮重新验证了在线权限。账号登记（远程 `/home/ubuntu/work/anc-data-tools/.local/config/accounts.json`）

| 品牌／系统 | 固定 CLI 的 `account_ref` | 非秘密资源标识 | 当前证据与边界 |
|---|---|---|---|
| BICEEK GA4 | `ga4.biceek` | `497540678` | 已有成功日报；USD，Los Angeles |
| BICEEK Google Ads | `google-ads.biceek` | `3508380986` | 已验收；USD，Los Angeles；无行不能一律解释为零 |
| BICEEK Meta | `meta-ads.biceek` | `4415468505361063` | 已验收；USD，New York；不在固定日报内 |
| BICEEK Shopify | `shopify.biceek` | `32305e.myshopify.com`；Shop `81924522263` | **COM 店，不含 EU**；USD，Los Angeles；历史订单已验收 |
| BICEEK Klaviyo | `klaviyo.biceek` | Account `THABgs`；Placed Order metric `T8miLT` | 10 月 26 日验收；USD，Los Angeles；不在固定日报内 |
| BICEEK GSC | `gsc.biceek` | `https://biceek.com/` | COM URL-prefix；已接通，历史搜索表现样本为空 |
| BICEEK Merchant | `merchant.biceek` | `5828687066` | 已验收；商品币种逐行保留，状态快照与每日表现分开 |
| RingConn GA4 主站 | `ga4.ringconn` | `377025799` | 主站商业表现入口；USD；最近已有调用返回 Los Angeles |
| RingConn GA4 辅助 | `ga4.ringconn_gtmss` / `_gen2` / `_ks` / `_jp` | `396032638` / `447802053` / `452118410` / `473588093` | 分别登记 Los Angeles / Nome / Anchorage / Shanghai；不得与主站相加 |
| RingConn 其他平台 | — | — | 项目明确记录 Shopify、Google Ads、Meta、Merchant、GSC、Klaviyo 未接通 |
| 两品牌 TikTok、Microsoft、Reddit | — | 未确认 | 找到通用脚本不等于找到这两个品牌的授权账号；本轮未确认可用连接 |

来源：部署验收（远程 `/home/ubuntu/work/anc-data-tools/docs/emw3-deployment-verification-2026-10-25.md`）、Klaviyo 验收（远程 `/home/ubuntu/work/anc-data-tools/docs/klaviyo-verification-2026-10-26.md`）、RingConn 项目说明（远程 `/home/ubuntu/work/emw3-anc/ringconn/README.md:74`）。

**2. Dashboard、日报与缓存复用**

| 现有来源 | 实际入口／存储 | 可复用方式与限制 |
|---|---|---|
| ANC 数据工具 | `/home/ubuntu/.agents/skills/anc-<平台>-data-readonly/scripts/anc-<平台>` | 独立短生命周期 CLI；显式账号路由。无需启动员工会话或日报调度 |
| BICEEK 固定日报 | GA4、Google Ads、Merchant；每日 PT 09:10 启动 | 聚合证据与原始 CLI 结果可优先读取；新增渠道不会自动进入日报 |
| RingConn 固定日报 | 主站 GA4；每日 PT 09:00 启动 | 保留正式 host 筛选、原生周期汇总及查询定义 |
| 日报证据目录 | `/home/ubuntu/work/emw3-anc/<品牌>/.local/daily-reports/outputs/<运行目录>/` | `aggregate-evidence.json`、`validation.json`、平台 JSON、报告 Markdown；不是统一数据库 schema |
| ANC 私有导出 | `/home/ubuntu/work/anc-data-tools/.local/exports/` | 可作为已有查询结果复用；不是持续刷新的业务缓存。JSONL 元数据首行不能计入业务行 |
| 现有 Web Dashboard | `/home/ubuntu/work/bintian/emw3-dashboard` | 首页及 tracking 源码存在 Bluevua 绑定；未确认两目标品牌的页面／账号映射 |
| Dashboard 数据库适配器 | `lib/db.ts`；`lib/klaviyo-dashboard.ts::loadDashboard()` | PostgreSQL；后者使用只读事务，但本轮未验证数据库内容及品牌归属，不建议直接搬用 |
| Dashboard tracking 缓存 | `lib/website-tracking-cache.ts::readTrackingCache()` | 默认 `.state/website-tracking`，可由 `WEBSITE_TRACKING_CACHE_DIR` 指定；尚未确认两品牌缓存 |
| 旧广告日报项目 | `/home/ubuntu/work/bintian/slack-ads-report` | 有 `meta_service.py`、`tiktok_service.py`、`bing_service.py`、`reddit_service.py`、`shopify_service.py`；目标品牌映射未确认，不直接运行其日报入口 |

日报调度与口径来源：统一日报流程（远程 `/home/ubuntu/work/emw3-anc/docs/daily-marketing-report.md`）。Dashboard 来源：数据库入口（远程 `/home/ubuntu/work/bintian/emw3-dashboard/lib/db.ts`）、Klaviyo 适配器（远程 `/home/ubuntu/work/bintian/emw3-dashboard/lib/klaviyo-dashboard.ts`）、tracking 缓存（远程 `/home/ubuntu/work/bintian/emw3-dashboard/lib/website-tracking-cache.ts`）。

**最近已核对的证据：**

- BICEEK 10 月 26 日输出（远程 `/home/ubuntu/work/emw3-anc/biceek/.local/daily-reports/outputs/2026-10-26-b5794b19-de6d-436c-8013-8b418edaa065/ga4-daily.json`）：GA4 `status=ok`、`coverage.complete=true`，区间 `2026-10-18, 2026-10-26)`，USD、Los Angeles。
- [RingConn 10 月 26 日聚合证据（远程 `/home/ubuntu/work/emw3-anc/ringconn/.local/daily-reports/outputs/2026-10-26-dd6a4349-9037-4f61-9e68-825599d0adf7/aggregate-evidence.json`）：主统计日 10 月 25 日；host 为 `ringconn.com`、`www.ringconn.com`；实际属性时区记录为 Los Angeles。旧规范 Anchorage 与此冲突，**时区变更生效日尚未确认**。
- BICEEK Klaviyo 的 Linux 验收记录：summary 返回 4 行、3 次请求、`ok` 且完整；这是验收样本，不是当前实时表现。

数据库不是这两个品牌唯一的获取来源，因此本轮未连接数据库；不能将源码出现的 `fact_campaign_performance`、`fact_flow_daily`、`fact_metric_daily` 等表名称为已核验的目标品牌 schema。

**3. 配置与凭证位置——仅路径和名称**

ANC 数据根默认 `/home/ubuntu/work/anc-data-tools/.local/`，可用 **`ANC_DATA_HOME`** 指定绝对路径；配置为 `config/accounts.json`、可选 `config/settings.json`。

| 用途 | 凭证路径，相对 ANC 数据根 |
|---|---|
| 两品牌 GA4、BICEEK GSC | `secrets/google/analytics-user.json` |
| BICEEK Ads、Merchant | `secrets/google/reader.json` |
| BICEEK Shopify | `secrets/shopify/biceek-anc-data-readonly.json` |
| BICEEK Meta | `secrets/meta-ads/biceek-anc.json` |
| BICEEK Klaviyo | `secrets/klaviyo/biceek-readonly.json` |

认证由固定 CLI 加载，不需要 Dashboard 读取或复制 token。`state/` 中可能有认证缓存，不可当营销数据缓存。配置契约（远程 `/home/ubuntu/work/anc-data-tools/docs/configuration.md`）

其他项目仅确认变量名称：

- Dashboard `.env.local`：`DATABASE_URL`、`KLAVIYO_DATABASE_URL`、`META_ACCESS_TOKEN`、`META_AD_ACCOUNT_ID`、`KLAVIYO_API_KEY`、`GOOGLE_ADS_CUSTOMER_ID` 等。
- 旧日报 `.env`：`GOOGLE_SERVICE_ACCOUNT_FILE`、`SHOPIFY_STORE`、`TIKTOK_ADVERTISER_IDS`、`BING_ADS_DEVELOPER_TOKEN` 等。

这些配置**未建立 BICEEK／RingConn 归属证据**，不可据此复用账号。

**4. 可执行获取配方**

以下为下一步可使用的命令，本轮没有执行。已使用对应 ANC 数据 Skill 核对命令契约。

```bash
# RingConn：指定周期汇总，自动比较上一等长周期
/home/ubuntu/.agents/skills/anc-ga4-data-readonly/scripts/anc-ga4 \
  reports summary --account ga4.ringconn --input-file - <<'JSON'
{"period":{"start":"2026-10-19","end":"2026-10-26"},"compare_previous":true}
JSON

# BICEEK：广告花费与转化
/home/ubuntu/.agents/skills/anc-google-ads-data-readonly/scripts/anc-google-ads \
  reports summary --account google-ads.biceek --input-file - <<'JSON'
{"days":7}
JSON

# BICEEK：Shopify 每日销售
/home/ubuntu/.agents/skills/anc-shopify-data-readonly/scripts/anc-shopify \
  reports query --account shopify.biceek --input-file - <<'JSON'
{"start":"2026-10-19","end":"2026-10-26","metrics":["orders","total_sales"],"timeseries":"day"}
JSON

# BICEEK：邮件与自动化流程
/home/ubuntu/.agents/skills/anc-klaviyo-data-readonly/scripts/anc-klaviyo \
  reports summary --account klaviyo.biceek --input-file - <<'JSON'
{"days":7}
JSON
```

Meta、GSC、Merchant 同样支持 `reports summary` + `{"days":7}`，分别使用 `anc-meta-ads`／`meta-ads.biceek`、`anc-gsc`／`gsc.biceek`、`anc-merchant`／`merchant.biceek`。

RingConn 正式站口径需要使用 `reports query`，在完整查询中加入：

```json
{
  "dimensionFilter": {
    "filter": {
      "fieldName": "hostName",
      "inListFilter": {
        "values": ["ringconn.com", "www.ringconn.com"]
      }
    }
  }
}
```

普通 `summary` 不应擅自加入该过滤参数。具体结构见 GA4 查询契约（远程 `/home/ubuntu/.agents/skills/anc-ga4-data-readonly/references/queries.md`）、Shopify 查询契约（远程 `/home/ubuntu/.agents/skills/anc-shopify-data-readonly/references/queries.md`）。

**5. 本地 Builder 接入建议**

1. **Cache-first**：先读已有聚合证据，检查实际日期、账号、筛选、币种、时区、`status`、`coverage`；目录日期不等于数据日期。
2. **仅补缺失区间**：通过固定 CLI 按日或小周期补取，不启动旧日报机器人或全量回填。保留完整 stdout 结构；失败、空集、partial、确认零值分别存储。
3. **项目隔离**：缓存键至少包含品牌、平台、`account_ref`、资源 ID、日期、时区、币种、指标和筛选／归因版本。服务端限制账号白名单；当前同一 OS 用户不构成强隔离。
4. **PoP／YoY**：PoP 使用紧邻的等长完整周期；YoY 单独查询去年同期，明确日历日期还是同星期对齐。缺去年数据就显示不可比较；用户数、会话、reach、unique 指标使用原生周期汇总，不能逐日相加。
5. **口径**：Ads cost micros 除以百万；Meta 保留 `7d_click + 1d_view` 和 impression 报告时间；Shopify 销售、GA4 收入、广告及 Klaviyo 归因收入分别呈现，不相加。近期结果允许有界重取以接收回补。

**已确认**

- 两品牌的 ANC 账号映射、固定 CLI、凭证文件位置及已有验收记录。
- 固定日报实际覆盖范围与 10 月 26 日已有聚合证据。
- RingConn 最近已有调用使用 Los Angeles；旧时区规范存在冲突。
- 当前 Dashboard 与旧日报存在其他平台代码，但不足以证明两品牌已接通。

**待验证**

- 本次通过本地 SSH 别名 `EMW3_ANC_AI` 查询 ANC 主机；远程路径与本地 Mac 路径不同。未跨查其他主机。
- 两品牌在现有 Dashboard 数据库中的租户／账户过滤、实际 schema、最新成功同步时间。
- TikTok、Microsoft、Reddit 的目标品牌账号与授权；BICEEK EU 独立资源。
- RingConn 时区变更生效日期及跨变更期同比可比性。
- 本地 Builder 的实际多项目接入与各来源在线权限复验尚未实施。获取方式已保存到本文。

## 从本地 Mac 调用与缓存落地（操作示例，未执行取数）

以下用已配置的 SSH 别名 `EMW3_ANC_AI` 在 ANC 内运行固定只读 CLI。认证仍由远程 CLI 加载；不复制远程 secrets 到本地，也不启动日报调度器。

### RingConn 主站：带正式域名过滤的每日趋势

```bash
ssh EMW3_ANC_AI '/home/ubuntu/.agents/skills/anc-ga4-data-readonly/scripts/anc-ga4 reports query --account ga4.ringconn --input-file -' <<'JSON'
{
  "period": {"start": "2026-10-20", "end": "2026-10-27"},
  "dimensions": ["date"],
  "metrics": ["sessions", "ecommercePurchases", "purchaseRevenue"],
  "dimensionFilter": {
    "filter": {
      "fieldName": "hostName",
      "inListFilter": {"values": ["ringconn.com", "www.ringconn.com"]}
    }
  },
  "page_size": 100,
  "max_rows": 1000
}
JSON
```

这是主站日趋势配方；整期 sessions/users 等原生指标需另取同样过滤的周期级报表（去掉日期维度），不能把日独立值直接相加。GA4 purchaseRevenue 应标明 GA4 口径，不当作已接通 Shopify 的店铺销售。

### Biceek：指定时段的 Google Ads 日数据

```bash
ssh EMW3_ANC_AI '/home/ubuntu/.agents/skills/anc-google-ads-data-readonly/scripts/anc-google-ads reports query --account google-ads.biceek --input-file -' <<'JSON'
{
  "resource": "customer",
  "period": {"start": "2026-10-20", "end": "2026-10-27"},
  "fields": ["segments.date", "metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions", "metrics.conversions_value"],
  "order_by": [{"field": "segments.date"}],
  "max_rows": 100
}
JSON
```

### 已有缓存的本地复用

- 先读取已核对的日报目录中的 `aggregate-evidence.json`、平台 JSON 和 `validation.json`；不要只复制最终 AI 报告 Markdown 作为数值源。
- 从 ANC 取回的文件应保存到前端私有状态目录，例如 `.state/ai-dashboard/projects/biceek/imports/<run-id>/`、`.state/ai-dashboard/projects/ringconn/imports/<run-id>/`，限制为当前用户可读写，并保持在 Git 之外。
- 原始文件先原样保存，再解析标准化；附上远程来源路径、提取时间、account_ref、property/account ID、原查询、筛选、日期边界、币种、时区、status、coverage、schema 版本。只缓存已读取数据，不复制认证缓存或秘密文件。
- stdout 有 `export_file` 时，完整业务数据仍可能位于远程私有导出文件中；按固定 CLI 的 JSON / JSONL 导出契约取回必要导出。预览少于总行数不代表缺失或零，partial 不能当作完整覆盖。

### 日期与比较规则

- Builder 目前显示起止日期均包含；ANC 的 `period.end` 排他。例如 Builder `2026-10-20 – 2026-10-26` 对应 CLI `[2026-10-20, 2026-10-27)`。上述固定示例与当前 Builder 默认周期一致，10 月 26 日可能尚未完成，应保留临时数据标记。
- `{"days":7}` 是各账号本地时区的最近 7 个完整自然日，不能用它冒充 Builder 指定的 Tue–Mon 周期。
- 沿用本地已确定的规则：PoP 为紧邻的等长前期，YoY 为去年同日历日期；分别获取，不把 CLI 默认上一周期当成 YoY。默认本期、PoP、YoY 的外部读取边界分别为 `[2026-10-20,2026-10-27)`、`[2026-10-13,2026-10-20)`、`[2025-10-20,2025-10-27)`。
- Biceek Meta 时区为 New York，GA4/Ads/Shopify/Klaviyo 为 Los Angeles；需要逐源保留。RingConn 主站历史时区冲突须在跨变更区间查询时附证据，不能静默重写历史。

### 建议的接入顺序

1. 先建立项目到账号白名单以及私有缓存命名空间。
2. Biceek 优先接 GA4、Google Ads、Shopify COM、Meta、Klaviyo；GSC/Merchant 作为独立自然搜索/商品组件，不混成广告收入。
3. RingConn 优先提供主站 GA4 overview 和渠道/日趋势；五个 property 独立可选或后续增加站点选择，不相加。广告花费/Shopify 数据保持未接通，不填假零。
4. 仅在来源身份、日期覆盖、单位与项目隔离验证通过后，启用 Builder 项目；本次未修改这些运行时开关。

核查范围：查询契约已从远程 Skill 的 `references/queries.md` 只读核对；以上示例未实际执行官方 API 请求，在线 token、历史时段可用性和跨期覆盖仍需在接入时逐来源验证。
