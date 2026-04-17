from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .shared import VlessProfile


def parse_vless_uri(uri: str) -> VlessProfile:
    parsed = urlparse(uri.strip())
    if parsed.scheme.lower() != "vless":
        raise ValueError("Unsupported URI scheme (expected vless://)")

    qs = parse_qs(parsed.query)

    def get_q(name: str, default: str = "") -> str:
        return unquote(qs.get(name, [default])[0])

    uuid = parsed.username or ""
    if not uuid:
        raise ValueError("VLESS URI has no UUID")

    return VlessProfile(
        uuid=uuid,
        port=int(parsed.port or 443),
        path=get_q("path", ""),
        host=get_q("host", ""),
        sni=get_q("sni", ""),
        security=get_q("security", "tls"),
        network=get_q("type", "ws"),
        flow=get_q("flow", ""),
        fp=get_q("fp", "chrome"),
        alpn=get_q("alpn", ""),
    )


def parse_vless_from_xray_json(path_or_json: str) -> VlessProfile:
    if Path(path_or_json).exists():
        data = json.loads(Path(path_or_json).read_text(encoding="utf-8"))
    else:
        data = json.loads(path_or_json)

    outbounds = data.get("outbounds", [])
    vless_ob = None
    for ob in outbounds:
        if str(ob.get("protocol", "")).lower() == "vless":
            vless_ob = ob
            break
    if not vless_ob:
        raise ValueError("No VLESS outbound found in xray JSON")

    vnext = vless_ob.get("settings", {}).get("vnext", [{}])[0]
    user = vnext.get("users", [{}])[0]
    stream = vless_ob.get("streamSettings", {})
    tls = stream.get("tlsSettings", {})
    ws = stream.get("wsSettings", {})
    headers_host = ws.get("headers", {}).get("Host", "")
    if isinstance(headers_host, list):
        headers_host = headers_host[0] if headers_host else ""

    alpn_val = tls.get("alpn", "")
    if isinstance(alpn_val, list):
        alpn_val = ",".join(str(x) for x in alpn_val)

    return VlessProfile(
        uuid=str(user.get("id", "")),
        port=int(vnext.get("port", 443)),
        path=str(ws.get("path", "")),
        host=str(ws.get("host", "") or headers_host or tls.get("serverName", "")),
        sni=str(tls.get("serverName", "")),
        security=str(stream.get("security", "tls")),
        network=str(stream.get("network", "ws")),
        flow=str(user.get("flow", "")),
        fp=str(tls.get("fingerprint", "chrome")),
        alpn=str(alpn_val),
    )


def load_vless_profile(source: str) -> VlessProfile:
    source = source.strip()
    if not source:
        raise ValueError("vless_source is empty")

    if source.startswith("vless://"):
        return parse_vless_uri(source)

    p = Path(source)
    if p.exists():
        txt = p.read_text(encoding="utf-8", errors="ignore").strip()
        if txt.startswith("vless://"):
            return parse_vless_uri(txt)
        return parse_vless_from_xray_json(str(p))

    if source.startswith("{"):
        return parse_vless_from_xray_json(source)

    raise ValueError("Unsupported vless_source. Use vless:// URI or xray JSON path")
