"""Webhook 通知：支持 bark / serverchan / wecom / dingtalk / generic。"""

import logging

import requests

logger = logging.getLogger("qnyz.notifier")


def send(notify_cfg, title, text, verify=True):
    ntype = (notify_cfg.get("type") or "generic").lower()
    url = notify_cfg.get("url")
    if not url:
        logger.warning("未配置 notify.url，跳过通知")
        return False
    try:
        if ntype == "bark":
            # 用 POST + JSON，避免 GET URL 过长（414）
            r = requests.post(
                url.rstrip("/"),
                json={"title": title, "body": text, "group": "青年驿站"},
                timeout=15, verify=verify)
        elif ntype == "serverchan":
            r = requests.post(url, data={"title": title, "desp": text},
                              timeout=15, verify=verify)
        elif ntype == "wecom":
            r = requests.post(
                url,
                json={"msgtype": "text", "text": {"content": f"{title}\n{text}"}},
                timeout=15, verify=verify,
            )
        elif ntype == "dingtalk":
            r = requests.post(
                url,
                json={"msgtype": "text", "text": {"content": f"{title}\n{text}"}},
                timeout=15, verify=verify,
            )
        else:  # generic
            r = requests.post(url, json={"title": title, "text": text},
                              timeout=15, verify=verify)
        r.raise_for_status()
        logger.info("通知已发送 (%s)", ntype)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("通知发送失败 (%s): %s", ntype, e)
        return False
