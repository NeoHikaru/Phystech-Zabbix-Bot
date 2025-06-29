import os
import httpx
import functools
from typing import Optional

ZBX_URL = os.getenv("ZABBIX_URL")
ZBX_USER = os.getenv("ZABBIX_USER")
ZBX_PASS = os.getenv("ZABBIX_PASS")
ZBX_TOKEN = os.getenv("ZABBIX_TOKEN")
ZBX_VERIFY_SSL = os.getenv("ZABBIX_VERIFY_SSL", "true").lower() in (
    "1",
    "true",
    "yes",
)


@functools.lru_cache()
def get_token() -> Optional[str]:
    """Retrieve API token or login using credentials."""
    if ZBX_TOKEN:
        return ZBX_TOKEN

    payload = {
        "jsonrpc": "2.0",
        "method": "user.login",
        "params": {"user": ZBX_USER, "password": ZBX_PASS},
        "id": 1,
    }
    r = httpx.post(ZBX_URL, json=payload, verify=ZBX_VERIFY_SSL)
    try:
        data = r.json()
    except ValueError:
        print("LOGIN not JSON:", r.text)
        return None

    if "result" in data:
        token = data["result"]
        print(f"LOGIN OK: len={len(token)}")
        return token

    print("LOGIN ERR:", data)
    return None


@functools.lru_cache()
def get_session() -> Optional[str]:
    """Return a web session ID using username/password if available."""
    if not (ZBX_USER and ZBX_PASS):
        return None

    payload = {
        "jsonrpc": "2.0",
        "method": "user.login",
        "params": {"user": ZBX_USER, "password": ZBX_PASS},
        "id": 1,
    }
    r = httpx.post(ZBX_URL, json=payload, verify=ZBX_VERIFY_SSL)
    try:
        data = r.json()
    except ValueError:
        print("SESSION LOGIN not JSON:", r.text)
        return None

    if "result" in data:
        session = data["result"]
        return session

    return None


async def call(method: str, params: dict):
    """Call Zabbix API method and return result list or empty list."""
    token = get_token()
    if not token:
        print("NO TOKEN → skip call", method)
        return []

    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1,
    }

    headers: dict[str, str] = {}
    if ZBX_TOKEN:
        headers["Authorization"] = f"Bearer {token}"
    else:
        payload["auth"] = token

    async with httpx.AsyncClient(verify=ZBX_VERIFY_SSL, headers=headers) as c:
        r = await c.post(ZBX_URL, json=payload)

    try:
        data = r.json()
    except ValueError:
        print(f"API {method}: not JSON", r.text[:200])
        return []

    if method == "problem.get":
        count = len(data.get("result", []))
        print(f"API {method}: status={r.status_code}, count={count}")

    if "result" in data and isinstance(data["result"], list):
        return data["result"]

    if "error" in data:
        print(f"API_ERR {method}:", data["error"])

    return []


async def chart_png(itemid: int, period: int = 3600) -> bytes:
    """Download a PNG chart for the given item and period."""
    url = f"{os.getenv('ZABBIX_WEB')}/chart2.php"
    params = {
        "itemids[]": itemid,
        "period": period,
        "width": 900,
        "height": 200,
        "name": "",
    }

    token = get_token()
    session_id = get_session() if ZBX_TOKEN else token
    headers: dict[str, str] = {}
    cookies = None
    if session_id:
        cookies = {"zbx_sessionid": session_id, "zbx_session": session_id}
        params["sid"] = session_id
    elif ZBX_TOKEN:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(verify=ZBX_VERIFY_SSL, headers=headers, cookies=cookies) as c:
        r = await c.get(url, params=params)
    r.raise_for_status()
    data = r.content
    if not data.startswith(b"\x89PNG"):
        raise ValueError("Invalid PNG data returned")
    return data
