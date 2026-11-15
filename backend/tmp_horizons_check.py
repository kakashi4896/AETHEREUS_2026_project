import json
import urllib.request
from app.official_data import build_horizons_url

url = build_horizons_url(
    target="10",
    start_time="2026-01-01+00:00:00",
    stop_time="2026-01-02+00:00:00",
    step="1h",
    observer="399",
)
print("URL:", url)
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
try:
    with urllib.request.urlopen(req, timeout=25) as r:
        data = r.read().decode("utf-8", "replace")
        print("HTTP_STATUS:", r.status)
        print("CONTENT_TYPE:", r.headers.get_content_type())
        print("LENGTH:", len(data))
        print("BODY_PREFIX:", data[:500].replace("\n", "\\n"))
        try:
            obj = json.loads(data)
            print("JSON_KEYS:", list(obj.keys())[:10])
            print("HAS_RESULT:", "result" in obj)
            print("RESULT_PREFIX:", str(obj.get("result", ""))[:200].replace("\n", "\\n"))
        except Exception as exc:  # pragma: no cover
            print("JSON_PARSE_ERROR:", repr(exc))
except Exception as exc:  # pragma: no cover
    print("REQUEST_ERROR:", repr(exc))
