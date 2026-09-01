"""青年驿站房源监控主程序（用户端公开接口版）。

用法:
  python monitor.py --once             跑一次，打印可预约房源
  python monitor.py --once --raw       跑一次，打印驿站列表原始 JSON
  python monitor.py --list-districts    打印当前所有区域名称后退出
  python monitor.py                     按 config.yaml 持续轮询监控
"""

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

from notifier import send as notify_send
from qnyz_client import QnyzClient

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.yaml")
STATE_FILE = os.path.join(HERE, "state.json")

logger = logging.getLogger("qnyz.monitor")


# ── 配置 & 状态 ────────────────────────────────────────────
def load_config(path):
    if not os.path.exists(path):
        sys.exit(f"配置文件不存在: {path}（请复制 config.example.yaml 为 config.yaml 并填写）")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f).get("stations", {})
        except (ValueError, OSError):
            pass
    return {}


def save_state(stations):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"stations": stations}, f, ensure_ascii=False, indent=2)


def setup_logging(log_file):
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(os.path.join(HERE, log_file), encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def to_int(v, default=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


# ── 拉取并标记驿站 ─────────────────────────────────────────
def get_stations(client, cfg):
    """拉取全部驿站，并标记每个是否『仅供集体申请』(groupOnly)。

    判定：housType=1 返回全部房源；housType=0 返回可供个人申请房源；
    在前者却不在后者的即为仅供集体申请。
    """
    f = cfg.get("filters") or {}
    name = f.get("name", "")
    ut = to_int(f.get("user_type"), 2)
    # 入住/离店日期传给接口 startTime/endTime，由服务器筛出“可住满整个区间”的驿站
    # （与官网筛选逻辑一致）。
    st, et = f.get("date_from", "") or "", f.get("date_to", "") or ""
    all_st = client.list_houses(name=name, user_type=ut, hous_type=1,
                                start_time=st, end_time=et)
    try:
        personal = client.list_houses(name=name, user_type=ut, hous_type=0,
                                       start_time=st, end_time=et)
        pids = {s.get("id") for s in personal}
    except Exception as e:  # noqa: BLE001
        logger.warning("获取个人可申请列表失败，无法标记集体申请: %s", e)
        pids = None
    for s in all_st:
        s["groupOnly"] = bool(pids is not None and s.get("id") not in pids)
    return all_st


# ── 过滤 ──────────────────────────────────────────────────
def apply_filters(stations, cfg):
    f = cfg.get("filters") or {}
    districts = [d for d in (f.get("districts") or []) if d]
    min_house_count = to_int(f.get("min_house_count"), 0)
    scope = (f.get("apply_scope") or "all").lower()  # all|personal|group
    out = []
    for s in stations:
        if districts and s.get("district") not in districts:
            continue
        if min_house_count and (to_int(s.get("houseCount"), 0) < min_house_count):
            continue
        if scope == "personal" and s.get("groupOnly"):
            continue
        if scope == "group" and not s.get("groupOnly"):
            continue
        out.append(s)
    return out


def available_dates(client, house_id, cfg):
    """返回该驿站可预约的日期列表 [(date, num), ...]，已按配置过滤。"""
    f = cfg.get("filters") or {}
    min_num = to_int(f.get("min_apply_number"), 1)
    date_from = f.get("date_from") or ""
    date_to = f.get("date_to") or ""
    try:
        cal = client.calendar(house_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("calendar(%s) 失败: %s", house_id, e)
        return []
    hits = []
    for x in cal:
        num = to_int(x.get("applyNumber"), 0)
        date = x.get("applyDate", "")
        if num < min_num:
            continue
        if date_from and date < date_from:
            continue
        if date_to and date > date_to:
            continue
        hits.append((date, num))
    return hits


# ── 展示 ──────────────────────────────────────────────────
def fmt_notify(s, dates, stay=None):
    """通知用的精简单行：名称[区域] (入住区间/可约日期) 电话。"""
    seg = f"{s.get('name','(未知)')}"
    if s.get("district"):
        seg += f"[{s['district']}]"
    if stay:  # 有入住/离店区间：显示可满足的区间
        seg += f" 可住 {stay}"
        if dates:  # 区间内最少房量
            seg += f"(最少{min(n for _, n in dates)}间)"
    else:      # 无区间：显示可约日期与总量
        total = sum(n for _, n in dates) if dates else 0
        first = dates[0][0] if dates else ""
        seg += f" 可约{total}间"
        if first:
            seg += f" 起{first}"
    return seg


def fmt_station(s, dates=None):
    parts = [s.get("name", "(未知)")]
    if s.get("district"):
        parts.append(f"[{s['district']}]")
    if s.get("houseCount") is not None:
        parts.append(f"共{s['houseCount']}间")
    if dates:
        total = sum(n for _, n in dates)
        ds = ", ".join(f"{d}×{n}" for d, n in dates[:6])
        more = "…" if len(dates) > 6 else ""
        parts.append(f"可约{total}间: {ds}{more}")
    if s.get("dizhi"):
        parts.append(s["dizhi"])
    if s.get("lxfs"):
        parts.append(f"电话{s['lxfs']}")
    return " ".join(str(p) for p in parts)


# ── 一轮检查 ──────────────────────────────────────────────
def run_once(client, cfg, state, do_notify=True, persist=True, notify_gate=None):
    """执行一轮检查。

    notify_gate: 可选回调，在真正推送/写状态前调用；返回 False 则本轮跳过
    推送与状态写入（用于旧配置线程被新配置取代时，避免用旧条件误推送）。
    """
    f = cfg.get("filters") or {}
    date_from = f.get("date_from", "") or ""
    date_to = f.get("date_to", "") or ""
    has_range = bool(date_from or date_to)

    stations = get_stations(client, cfg)
    stations = apply_filters(stations, cfg)
    logger.info("符合基础条件的驿站 %d 个，查询可预约情况…", len(stations))

    check_avail = cfg.get("check_availability", True)
    workers = to_int(cfg.get("concurrency"), 10)

    results = []  # (station, dates)
    if check_avail:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            date_lists = list(ex.map(
                lambda s: available_dates(client, s["id"], cfg), stations))
        for s, dates in zip(stations, date_lists):
            # 有入住/离店区间时：服务器已筛出“可住满整个区间”的驿站，列表内即合格
            # （日历仅用于展示区间内每日房量）。无区间时：按日历逐日可约判定。
            if has_range or dates:
                results.append((s, dates))
    else:
        results = [(s, None) for s in stations]

    # 打印当前可用
    for s, dates in results:
        logger.info("可预约: %s", fmt_station(s, dates))

    # 去重：有区间时按“该驿站能否满足此入住区间”去重；无区间时按可约日期集合去重
    stay_token = f"stay:{date_from}~{date_to}"
    new_hits = []
    for s, dates in results:
        sid = str(s["id"])
        if has_range:
            cur = [stay_token]
        else:
            cur = sorted(d for d, _ in dates) if dates else ["_available_"]
        prev = set(state.get(sid, {}).get("dates", []))
        fresh = [d for d in cur if d not in prev]
        if fresh:
            new_hits.append((s, dates, fresh))
        state[sid] = {"name": s.get("name"), "dates": cur}

    # 清理已无可约的驿站状态
    live_ids = {str(s["id"]) for s, _ in results}
    for sid in list(state.keys()):
        if sid not in live_ids:
            del state[sid]

    gate_ok = notify_gate() if notify_gate else True
    if not gate_ok:
        logger.info("本轮结果已被新配置取代，跳过推送与状态写入")
        return results, new_hits

    if new_hits and do_notify:
        stay = f"{date_from}→{date_to}" if has_range else None
        lines = [fmt_notify(s, dates, stay=stay) for s, dates, _ in new_hits]
        if has_range:
            title = f"青年驿站 {len(new_hits)} 处可住 {date_from}→{date_to}"
        else:
            title = f"青年驿站 {len(new_hits)} 处新增可预约"
        body = "\n".join(lines)
        max_len = to_int(cfg.get("notify_max_len"), 1500)
        if len(body) > max_len:
            shown = 0
            kept = []
            for ln in lines:
                if shown + len(ln) + 1 > max_len:
                    break
                kept.append(ln)
                shown += len(ln) + 1
            kept.append(f"…还有 {len(lines) - len(kept)} 处（详见日志）")
            body = "\n".join(kept)
        notify_send(cfg.get("notify") or {}, title, body,
                    verify=cfg.get("verify_ssl", True))
    elif new_hits:
        logger.info("发现 %d 处新增（--no-notify 未推送）", len(new_hits))
    else:
        logger.info("无新增可预约房源")

    if persist:
        save_state(state)
    return results, new_hits


# ── main ──────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="青年驿站房源监控（用户端公开接口）")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--once", action="store_true", help="只跑一次")
    ap.add_argument("--raw", action="store_true", help="配合 --once，打印驿站列表原始 JSON")
    ap.add_argument("--list-districts", action="store_true", help="打印当前所有区域名称后退出")
    ap.add_argument("--no-notify", action="store_true", help="不发送通知（调试）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    # 环境变量覆盖（供 GitHub Actions 等场景注入密钥，避免写进仓库）
    if os.environ.get("QNYZ_NOTIFY_URL"):
        cfg.setdefault("notify", {})
        cfg["notify"]["url"] = os.environ["QNYZ_NOTIFY_URL"]
        if os.environ.get("QNYZ_NOTIFY_TYPE"):
            cfg["notify"]["type"] = os.environ["QNYZ_NOTIFY_TYPE"]
    setup_logging(cfg.get("log_file"))

    client = QnyzClient(
        base_url=cfg.get("base_url", "https://qnyz.shyouth.net/qnyzApi"),
        verify=cfg.get("verify_ssl", True),
    )

    if args.list_districts:
        stations = client.list_houses()
        from collections import Counter
        for d, c in Counter(s.get("district") for s in stations).most_common():
            print(f"{d}\t{c}")
        return

    if args.raw:
        stations = apply_filters(get_stations(client, cfg), cfg)
        print(json.dumps(stations, ensure_ascii=False, indent=2))
        return

    state = load_state()

    if args.once:
        run_once(client, cfg, state, do_notify=not args.no_notify)
        return

    interval = to_int(cfg.get("interval"), 300)
    logger.info("开始持续监控，间隔 %d 秒", interval)
    while True:
        try:
            run_once(client, cfg, state, do_notify=not args.no_notify)
        except Exception as e:  # noqa: BLE001
            logger.exception("本轮出错: %s", e)
        time.sleep(interval)


if __name__ == "__main__":
    main()
