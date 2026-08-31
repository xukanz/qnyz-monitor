"""青年驿站用户端 (qnyz.shyouth.net/qnyzApi) 客户端。

该用户端接口为公开接口，无需登录/验证码：
  - POST /hous/housListBatch  房源（驿站）列表
  - GET  /hous/calendar?id=   某驿站按日期的可预约数量（applyNumber）
"""

import logging

import requests

logger = logging.getLogger("qnyz.client")


class QnyzClient:
    def __init__(self, base_url="https://qnyz.shyouth.net/qnyzApi", timeout=45, verify=True):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify = verify
        if verify is False:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session = requests.Session()
        self.session.verify = verify
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (qnyz-monitor)",
            "Accept": "application/json, text/plain, */*",
        })

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
        r = self.session.post(
            f"{self.base_url}/hous/housListBatch",
            json=payload,
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 200:
            raise RuntimeError(f"housListBatch 失败: {data.get('msg')}")
        return data.get("data") or []

    def calendar(self, house_id):
        """GET /hous/calendar?id=，返回 [{applyDate, applyNumber}, ...]。"""
        r = self.session.get(
            f"{self.base_url}/hous/calendar",
            params={"id": house_id},
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 200:
            raise RuntimeError(f"calendar({house_id}) 失败: {data.get('msg')}")
        return data.get("data") or []
