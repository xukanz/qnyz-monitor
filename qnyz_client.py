"""青年驿站用户端 (qnyz.shyouth.net/qnyzApi) 客户端。

该用户端接口为公开接口，无需登录/验证码：
  - POST /hous/housListBatch  房源（驿站）列表
  - GET  /hous/calendar?id=   某驿站按日期的可预约数量（applyNumber）
"""

import logging
import time

import requests

logger = logging.getLogger("qnyz.client")


class QnyzClient:
    def __init__(self, base_url="https://qnyz.shyouth.net/qnyzApi", timeout=(15, 45),
                 verify=True, retries=4, backoff=5):
        self.base_url = base_url.rstrip("/")
        # timeout 可为 (连接超时, 读取超时)：连接超时设短些，
        # 这样对方不通时能快速失败并重试，而不是干等 45 秒。
        self.timeout = timeout
        self.verify = verify
        self.retries = retries      # 网络抖动/超时时的重试次数
        self.backoff = backoff      # 重试间隔基数（秒），线性递增
        if verify is False:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session = requests.Session()
        self.session.verify = verify
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (qnyz-monitor)",
            "Accept": "application/json, text/plain, */*",
        })

    def _send(self, method, url, **kwargs):
        """带重试的请求：对连接超时/读超时/连接错误自动重试若干次。"""
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                return self.session.request(method, url, timeout=self.timeout, **kwargs)
            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError) as e:
                last = e
                if attempt < self.retries:
                    wait = self.backoff * attempt
                    logger.warning("请求 %s 失败(第%d次): %s，%ds 后重试",
                                   url, attempt, type(e).__name__, wait)
                    time.sleep(wait)
        raise last

    def list_houses(self, name="", user_type=2, hous_type=1,
                    districts=None, start_time="", end_time="", into_per_num=""):
        """POST /hous/housListBatch，返回驿站列表 (list[dict])。

        districts 服务端需区域编码，通常留空由客户端按 district 名称过滤。
        """
        payload = {
            "name": name or "",
            "userType": user_type,
            "housType": hous_type,
            "districts": districts or [],
            "startTime": start_time or "",
            "endTime": end_time or "",
            "intoPerNum": into_per_num or "",
        }
        r = self._send("POST", f"{self.base_url}/hous/housListBatch", json=payload)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 200:
            raise RuntimeError(f"housListBatch 失败: {data.get('msg')}")
        return data.get("data") or []

    def calendar(self, house_id):
        """GET /hous/calendar?id=，返回 [{applyDate, applyNumber}, ...]。"""
        r = self._send("GET", f"{self.base_url}/hous/calendar",
                       params={"id": house_id})
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 200:
            raise RuntimeError(f"calendar({house_id}) 失败: {data.get('msg')}")
        return data.get("data") or []
