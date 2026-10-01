# 独立 KOL Tracker 部署

生产唯一入口 `kol_tracker.py`，不启动、不导入、不重启原来的 app。
Python 3.10+、Linux systemd、可使用 sudo 的部署用户。

## 首次安装（EC2）

```bash
git clone --branch codex/kol-tracker-audit --single-branch \
  https://github.com/bintian-elle/slack-ads-report.git /home/ubuntu/kol-tracker
cd /home/ubuntu/kol-tracker
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-kol-tracker.txt
mkdir -p credentials
```

把已有 `.env` 和 Google service account JSON **复制**到这个独立目录；不要
移动旧文件，不要修改旧程序的 token、虚拟环境或服务。也可在新的 `.env`
中将 `GOOGLE_SERVICE_ACCOUNT_FILE` 设置为已有凭证的绝对路径。
`.env` 必需键：

- `KOL_TRACKER_GOOGLE_SHEETS_LINK`
- `KOL_TRACKER_SLACK_CHANNEL_ID`
- `KOL_TRACKER_SLACK_BOT_TOKEN`（history + replies 读取权限，bot 已加入频道）
- `META_ACCESS_TOKEN`、`META_AD_ACCOUNT_ID`
- `TIKTOK_ACCESS_TOKEN`、`TIKTOK_ADVERTISER_IDS`（逗号分隔或 JSON 列表）
- `GOOGLE_SERVICE_ACCOUNT_FILE`（默认 `credentials/google-service-account.json`）

可选 `KOL_TRACKER_TIKTOK_ACCESS_TOKEN` 优先于已有 TikTok Token。
状态默认写到新目录 `data/processed/kol_tracker_runtime`，可设置
`KOL_TRACKER_STATE_DIR` 为独立持久目录。不要放在旧程序共享输出位置。
Google service account 需要此原生 Sheet 的编辑权限。

```bash
chmod 600 .env credentials/google-service-account.json
.venv/bin/python kol_tracker.py check
.venv/bin/python kol_tracker.py poll
.venv/bin/python kol_tracker.py daily
```

以上都是只读预览（私有状态/备份会写本地，Slack 游标不推进）。核对日志后启用：

```bash
.venv/bin/python kol_tracker.py poll --apply
.venv/bin/python kol_tracker.py daily --apply
bash deploy/install_kol_tracker.sh
```

安装器只新增/更新 `kol-tracker-*` 四个 systemd unit。Slack 每 5 分钟；广告
每天 `America/Chicago` 08:00，自动适应夏令时。`Persistent=true` 允许宕机后补跑。
共用文件锁防止 Slack 和每日任务同时修改表格。轮询遇到占用则跳过，每日任务等待锁释放。

## 更新这个独立目录

```bash
cd /home/ubuntu/kol-tracker
sudo systemctl stop kol-tracker-poll.timer kol-tracker-daily.timer
# 等当前服务完成，避免运行中更换代码：
while systemctl is-active --quiet kol-tracker-poll.service || systemctl is-active --quiet kol-tracker-daily.service; do
  sleep 5
done
git pull --ff-only origin codex/kol-tracker-audit
.venv/bin/python -m pip install -r requirements-kol-tracker.txt
.venv/bin/python -m unittest discover -s tests
.venv/bin/python kol_tracker.py check
bash deploy/install_kol_tracker.sh
```

若服务仍 active，等待其完成后再 pull，不要杀死正在写 Sheet 的任务。
不要执行任何旧 app 服务的 restart。首次部署用 clone；更新只在此独立 checkout。

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
  TikTok 当前广告详情权限不足，新行 F 暂空，已有人工 Status 永不改动。
- 按 Launch Date 插入到相应位置，保留底部预置 briefs、原生格式、验证和 N 的 ID。
- Meta 每日 J:S 使用广告账户时区前一日截止的 Insights；只精确 Code 匹配。
- TikTok 每日 H:M 使用当前请求时刻的 lifetime 报表；N 有 ID 则优先读取。
  没 ID 的历史 paused 行不追查。新行仅在名字／帖子用户名及日期窗口（-1 至
  +14 天）归为同一广告名称组且没有跨行争用时绑定，并写入 N。这是名称交叉
  核验而非精确 Ad Code→Ad ID 证明，仍可能因广告命名错误而误判。
- 多广告组 Spend、Purchase、Views、Hook Rate 汇总；Reach、ROAS 保留，直到
  有可靠 API 去重 Reach／收入汇总。不把未匹配或 API 失败写成零。
- 不依赖任何本机历史核验 JSON；凭证和表内 N 的绑定足够启动生产入口。
- EC2 安装和 systemd 运行验证由用户执行；本机只读 API 预览不能证明 EC2 权限。
