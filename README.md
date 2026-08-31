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
- `filters.name`：驿站名关键字（服务端模糊匹配）。
- `filters.apply_scope`：申请类型 —— `all` 全部 / `personal` 仅个人可申请（排除仅集体）/ `group` 仅供集体申请。
  （判定方式：`housType=1` 返回全部房源，`housType=0` 返回可供个人申请房源，差集即“仅供集体申请”。网页表格会给这些驿站打「仅集体」标。）
- `filters.min_apply_number` / `date_from` / `date_to`：可预约的数量与日期范围。
- `notify.type`：`bark` / `serverchan` / `wecom` / `dingtalk` / `generic`。

## 使用

```bash
# 查看当前所有区域（帮助填 districts）
python monitor.py --list-districts

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
- **筛选条件**：入住起止日期、每日可约数下限、名称关键字、轮询间隔、区域多选。
- **🔍 预览当前可约**：立即按条件查询并列表展示（不发通知、不影响去重状态）。
- **▶ 启动监控 / ⏹ 停止监控**：后台按间隔轮询，命中新增可预约就走 Bark 推送。
- 状态区显示运行中/已停止、上次检查时间、可约驿站数、本轮新增数。

> 通知（Bark 等）与 `verify_ssl` 仍从 `config.yaml` 读取；网页只控制过滤条件与轮询间隔。
> 点「停止」若正处于一次网络抓取中，会在当前这轮结束后停下（几秒内）。

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
- `config.example.yaml` — 配置模板
