# 青年驿站房源监控 (qnyz-monitor)

定时监控上海青年驿站（`qnyz.shyouth.net` 用户端）的**可预约房源**，按条件过滤，发现新增可预约时通过 Webhook 推送到手机。

> 数据来自用户端**公开接口** `https://qnyz.shyouth.net/qnyzApi`，**无需登录、无需验证码**：
> - `POST /hous/housListBatch` — 驿站列表（名称、区域、地址、总房间数等）
> - `GET  /hous/calendar?id=` — 某驿站每日可预约数量 `applyNumber`（即“剩余房源”）

## 安装

```bash
git clone https://github.com/xukanz/qnyz-monitor.git
cd qnyz-monitor
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 配置

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml：填 notify（如 Bark key），按需设置 filters
```

关键配置项：
- `check_availability`：`true` 监控真正可预约（查每个驿站日历），`false` 只看列表/总房间数。
- `filters.districts`：按区域名过滤，如 `["浦东新区","徐汇区"]`（用 `--list-districts` 查可选值）。
- `filters.name`：驿站名关键字（服务端模糊匹配，只能填一个）。
- `filters.only_stations`：只盯指定的一家/几家，如 `["友间","1024"]`。每项可写驿站名关键字（名称包含即命中）或驿站 id（完全相等），留空=不限（用 `--list-stations` 查 id/名称）。
- `filters.apply_scope`：申请类型 —— `all` 全部 / `personal` 仅个人可申请（排除仅集体）/ `group` 仅供集体申请。
  （判定方式：`housType=1` 返回全部房源，`housType=0` 返回可供个人申请房源，差集即“仅供集体申请”。网页表格会给这些驿站打「仅集体」标。）
- `filters.min_apply_number` / `date_from` / `date_to`：可预约的数量与日期范围。
- `notify.type`：`bark` / `serverchan` / `wecom` / `dingtalk` / `generic`。

## 使用

```bash
# 查看当前所有区域（帮助填 districts）
python monitor.py --list-districts

# 查看所有驿站的 id/名称/区域（帮助填 only_stations）
python monitor.py --list-stations

# 跑一次：打印当前可预约的驿站
python monitor.py --once --no-notify

# 跑一次：打印驿站列表原始 JSON
python monitor.py --once --raw

# 持续监控
python monitor.py

# 后台常驻
nohup python monitor.py >> qnyz_monitor.log 2>&1 &
```

## 网页控制台（推荐）

在网页上选区域/日期，点按钮启动或停止监控：

```bash
source .venv/bin/activate
python app.py                 # 默认 http://127.0.0.1:8765
# 局域网访问：python app.py --host 0.0.0.0 --port 8765
```

打开 `http://127.0.0.1:8765`：
- **筛选条件**：入住起止日期、每日可约数下限、申请类型、轮询间隔、区域多选。
- **指定驿站**：可勾选只盯某一家或某几家（不勾=全部）。列表带搜索框，并跟着区域/申请类型实时收窄；因条件变化而不再符合的会自动取消勾选。网页不提供「名称关键字」，按名字筛用这里勾选即可（`filters.name` 仍供 CLI / Actions 使用）。
- **🔍 预览当前可约**：立即按条件查询并列表展示（不发通知、不影响去重状态）。
- **▶ 启动监控 / ⏹ 停止监控**：后台按间隔轮询，命中新增可预约就走 Bark 推送。
- 状态区显示运行中/已停止、上次检查时间、可约驿站数、本轮新增数。

> 通知（Bark 等）与 `verify_ssl` 仍从 `config.yaml` 读取；网页只控制过滤条件与轮询间隔。
> 点「停止」若正处于一次网络抓取中，会在当前这轮结束后停下（几秒内）。

## GitHub Actions 定时监控（电脑关机也能推送）

不想一直开着电脑？用 GitHub Actions 让监控跑在 GitHub 服务器上，按 cron 定时检查、命中就推送。仓库已内置工作流 `.github/workflows/monitor.yml`。自己搭建步骤：

1. **Fork 或使用本仓库**（你自己的账号下）。

2. **添加 Bark（或其他 Webhook）地址为仓库 Secret**，名字必须是 `BARK_URL`：
   ```bash
   gh secret set BARK_URL --repo <你的用户名>/qnyz-monitor --body "https://api.day.app/<你的key>"
   ```
   或网页：仓库 → Settings → Secrets and variables → Actions → New repository secret，Name 填 `BARK_URL`。
   > Secret 加密存储、不可读回、不进代码；工作流运行时注入为环境变量 `QNYZ_NOTIFY_URL`。
   > 用其他通知渠道：把 `BARK_URL` 填成对应 webhook，并在对应 `config.ci.*.yaml` 改 `notify.type`（bark/serverchan/wecom/dingtalk/generic）。

3. **改监控条件**：编辑对应的 `config.ci.*.yaml`（区域 `districts`、入住/离店 `date_from`/`date_to`、申请类型 `apply_scope` 等），`git push` 即生效。

4. **定时怎么来**：见下面「定时触发（重要）」——本仓库实测 GitHub 自带 `schedule` 不触发，实际用外部服务调 API。

5. **启用 / 手动触发 / 停用**：
   ```bash
   gh workflow enable  monitor.yml --repo <你的用户名>/qnyz-monitor   # 启用
   gh workflow run     monitor.yml --repo <你的用户名>/qnyz-monitor   # 手动跑一次
   gh workflow disable monitor.yml --repo <你的用户名>/qnyz-monitor   # 暂停
   ```
   也可在仓库 **Actions** 页面点 Run workflow / Enable / Disable。

### 定时触发（重要）

**GitHub 自带的 `schedule` 不可靠。** 本仓库实测：`*/10` 和每 30 分钟的 cron **一次都没触发过**（GitHub 官方明确说 schedule 不保证执行，高频 cron 常被整体跳过）。工作流里保留 `schedule` 只作备份。

**实际做法：用外部定时服务按间隔调用 GitHub API 触发 `workflow_dispatch`。**

1. **创建细粒度 PAT**：https://github.com/settings/personal-access-tokens/new
   - Repository access → Only select repositories → 选本仓库
   - Permissions → Repository permissions → **Actions: Read and write**（其他都不给）
   - 生成后复制 `github_pat_...`（只显示一次）

2. **在定时服务里建任务**（如 https://cron-job.org ，免费）：

   | 项 | 值 |
   |---|---|
   | URL | `https://api.github.com/repos/<你的用户名>/qnyz-monitor/actions/workflows/monitor.yml/dispatches` |
   | Method | `POST` |
   | Schedule | 按需，如每 5 / 10 分钟 |
   | Body | `{"ref":"main"}` |

   Request headers：
   ```
   Accept: application/vnd.github+json
   Authorization: Bearer github_pat_你的token
   X-GitHub-Api-Version: 2022-11-28
   Content-Type: application/json
   ```

   成功时 GitHub 返回 **204 No Content**（无响应体即正常）。

> - PAT 等同密码：只放在定时服务的配置里，**不要提交进仓库**；泄露就去 GitHub 设置 Revoke。
> - PAT **到期后定时会静默失效**（定时服务会显示 401），记得续期。
> - 改监控条件只需改 `config.ci.*.yaml` 并 push，**无需改动定时服务**。

### 同时监控多组条件（matrix）

工作流用 `strategy.matrix` 并行跑多组条件，每组一个配置文件、**各自独立的去重缓存**（key 前缀含组名，互不覆盖）。内置两组示例：

- `config.ci.a.yaml` — A 组
- `config.ci.b.yaml` — B 组

**增删组**：在 `.github/workflows/monitor.yml` 的 `matrix.include` 里加/减一项，并配一个对应的 `config.ci.<名>.yaml`：
```yaml
matrix:
  include:
    - name: A
      config: config.ci.a.yaml
    - name: B
      config: config.ci.b.yaml
    # - name: C            # 再加一组就照这样加
    #   config: config.ci.c.yaml
```
> 各组推送标题自带入住/离店日期，手机上可区分是哪组。

**说明与注意：**
- 去重状态 `state.json` 用 `actions/cache` 跨次保存（滚动缓存），因此定时版同样"只推新增、不重复"；每组用独立缓存 key（`qnyz-state-<组名>-`）。
- GitHub 定时任务**不保证准点**，高峰期常延迟几分钟，`*/10` 实际间隔会有波动。
- 仓库 **60 天无提交**会被 GitHub 自动停用定时任务，重新 enable 或偶尔 push 一下即可保活。
- `config.ci.yaml` 里 `verify_ssl: false`：CI 环境为公开只读数据，关闭证书校验以规避潜在证书链问题；本地如需校验可用 `config.yaml` 单独设置。

## 去重逻辑

`state.json` 记录每个驿站上次已通知的**可预约日期集合**；仅当出现**新的可预约日期**时才推送，不会重复轰炸。已无可约的驿站会从状态中清除，之后再次可约会重新通知。删除 `state.json` 可重置。

## 字段说明（housListBatch 返回）

`id, xmid, name(驿站名), dizhi(地址), lxfs(电话), district(区域), houseCount(总房间数),
zujin(租金), mianji(面积), peitaosheshi(配套), blurb(简介), longitude/latitude, isOnShelf(是否上架), …`

日历 `calendar` 返回 `[{applyDate, applyNumber}, ...]`，`applyNumber>0` 即该日期可预约。

## 结构

- `app.py` — 网页控制台（Flask，选条件 + 启停 + 预览）
- `qnyz_client.py` — 公开接口封装（列表 + 日历）
- `notifier.py` — Webhook 推送
- `monitor.py` — 轮询主循环 + 过滤 + 去重 + 通知（CLI 与网页共用 `run_once`）
- `config.example.yaml` — 本地配置模板（复制为 `config.yaml`）
- `config.ci.a.yaml` / `config.ci.b.yaml` — GitHub Actions 各组配置（不含密钥）
- `.github/workflows/monitor.yml` — 定时监控工作流（matrix 并行多组）
