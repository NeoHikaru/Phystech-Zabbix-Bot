import os, httpx, functools

ZBX_URL   = os.getenv("ZABBIX_URL")
ZBX_USER  = os.getenv("ZABBIX_USER")
ZBX_PASS  = os.getenv("ZABBIX_PASS")
ZBX_TOKEN = os.getenv("ZABBIX_TOKEN")
ZBX_VERIFY_SSL = os.getenv("ZABBIX_VERIFY_SSL", "true").lower() in ("1", "true", "yes")

@functools.lru_cache()
def get_token() -> str | None:
    
    if ZBX_TOKEN:                      # если используется API токен
        return ZBX_TOKEN

    # иначе получаем session-token
    payload = {
        "jsonrpc": "2.0",
        "method":  "user.login",
        "params":  {"user": ZBX_USER, "password": ZBX_PASS},
        "id":      1,
    }
    r = httpx.post(ZBX_URL, json=payload, verify=ZBX_VERIFY_SSL)
    try:
        data = r.json()
    except ValueError:
        print("LOGIN not JSON:", r.text)
        return None

    if "result" in data:
        tok = data["result"]
        print(f"LOGIN OK: len={len(tok)}")
        return tok

    print("LOGIN ERR:", data)
    return None


async def call(method: str, params: dict):
    
    token = get_token()
    if not token:
        print("NO TOKEN → skip call", method)
        return []

    
    payload = {
        "jsonrpc": "2.0",
        "method":  method,
        "params":  params,
        "id":      1,
    }

    
    headers = {}
    if ZBX_TOKEN:                      # Bearer
        headers["Authorization"] = f"Bearer {token}"
    else:                              # sessionid
        payload["auth"] = token

    async with httpx.AsyncClient(verify=ZBX_VERIFY_SSL, headers=headers) as c:
        r = await c.post(ZBX_URL, json=payload)

    try:
        data = r.json()
    except ValueError:
        print(f"API {method}: not JSON", r.text[:200])
        return []

    if method == "problem.get":
        print(f"API {method}: status={r.status_code}, count={len(data.get('result', []))}")

    if "result" in data and isinstance(data["result"], list):
        return data["result"]

    if "error" in data:
        print(f"API_ERR {method}:", data["error"])

    return []

async def chart_png(itemid: int, period: int = 3600) -> bytes:
    """
    Скачивает PNG-график itemid за period секунд.
    """
    url = f"{os.getenv('ZABBIX_WEB')}/chart2.php"
    params = {
        "itemids[]": itemid,
        "period":    period,
        "width":     900,
        "height":    200,
        "name":      ""            # без заголовка
    }
    headers = {"Authorization": f"Bearer {get_token()}"}  # токен в заголовке
    async with httpx.AsyncClient(verify=ZBX_VERIFY_SSL, headers=headers) as c:
        r = await c.get(url, params=params)
    r.raise_for_status()
    return r.content
