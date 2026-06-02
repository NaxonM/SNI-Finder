from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .shared import VlessProfile

SUPPORTED_SCHEMES = ("vless", "trojan")


def parse_proxy_uri(uri: str) -> VlessProfile:
    parsed = urlparse(uri.strip())
    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_SCHEMES:
        raise ValueError(f"Unsupported URI scheme (expected one of {SUPPORTED_SCHEMES})")

    qs = parse_qs(parsed.query)

    def get_q(name: str, default: str = "") -> str:
        return unquote(qs.get(name, [default])[0])

    cred = unquote(parsed.username or "")
    if not cred:
        raise ValueError(f"{scheme.upper()} URI is missing credentials (UUID/password)")

    return VlessProfile(
        uuid=cred if scheme == "vless" else "",
        password=cred if scheme == "trojan" else "",
        protocol=scheme,
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


# Kept for backwards compatibility; delegates to the unified parser.
def parse_vless_uri(uri: str) -> VlessProfile:
    return parse_proxy_uri(uri)


def parse_vless_from_xray_json(path_or_json: str) -> VlessProfile:
    if Path(path_or_json).exists():
        data = json.loads(Path(path_or_json).read_text(encoding="utf-8"))
    else:
        data = json.loads(path_or_json)

    outbounds = data.get("outbounds", [])
    proxy_ob = None
    for ob in outbounds:
        proto = str(ob.get("protocol", "")).lower()
        if proto in SUPPORTED_SCHEMES:
            proxy_ob = ob
            break
    if not proxy_ob:
        raise ValueError(f"No {'/'.join(SUPPORTED_SCHEMES)} outbound found in xray JSON")

    protocol = str(proxy_ob.get("protocol", "vless")).lower()
    settings = proxy_ob.get("settings", {})
    stream = proxy_ob.get("streamSettings", {})
    tls = stream.get("tlsSettings", {})

    if protocol == "vless":
        vnext = settings.get("vnext", [{}])[0]
        user = vnext.get("users", [{}])[0]
        address_port = vnext
        uuid = str(user.get("id", ""))
        password = ""
        flow = str(user.get("flow", ""))
    else:
        server = settings.get("servers", [{}])[0]
        address_port = server
        uuid = ""
        password = str(server.get("password", ""))
        flow = ""

    network = str(stream.get("network", "ws"))
    transport_settings = stream.get(f"{network}Settings", {}) or {}
    path = str(transport_settings.get("path", "") or transport_settings.get("serviceName", ""))
    headers_host = transport_settings.get("headers", {}).get("Host", "") if isinstance(transport_settings.get("headers"), dict) else ""
    if isinstance(headers_host, list):
        headers_host = headers_host[0] if headers_host else ""
    host = str(transport_settings.get("host", "") or headers_host or tls.get("serverName", ""))

    alpn_val = tls.get("alpn", "")
    if isinstance(alpn_val, list):
        alpn_val = ",".join(str(x) for x in alpn_val)

    return VlessProfile(
        uuid=uuid,
        password=password,
        protocol=protocol,
        port=int(address_port.get("port", 443)),
        path=path,
        host=host,
        sni=str(tls.get("serverName", "")),
        security=str(stream.get("security", "tls")),
        network=network,
        flow=flow,
        fp=str(tls.get("fingerprint", "chrome")),
        alpn=str(alpn_val),
    )


def load_vless_profile(source: str) -> VlessProfile:
    source = source.strip()
    if not source:
        raise ValueError("vless_source is empty")

    for scheme in SUPPORTED_SCHEMES:
        if source.startswith(f"{scheme}://"):
            return parse_proxy_uri(source)

    p = Path(source)
    if p.exists():
        txt = p.read_text(encoding="utf-8", errors="ignore").strip()
        for scheme in SUPPORTED_SCHEMES:
            if txt.startswith(f"{scheme}://"):
                return parse_proxy_uri(txt)
        return parse_vless_from_xray_json(str(p))

    if source.startswith("{"):
        return parse_vless_from_xray_json(source)

    raise ValueError("Unsupported source. Use vless://, trojan://, or an xray JSON path")
