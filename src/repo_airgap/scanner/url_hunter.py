import ipaddress
import re
from urllib.parse import urlsplit

URL = re.compile(r"(?:https?|wss?)://[^\s\"'`<>\\)\]}]+", re.I)
BARE = re.compile(r"[\"']((?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,63})(?::(\d{1,5}))?(?:/[^\"']*)?[\"']")
IP = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
CLIENT = re.compile(
    r"(?<![\w$])fetch\s*\(|axios(?:\.|\s*\()|(?:requests|httpx)\."
    r"|urllib\.(?:request\.)?(?:urlopen|urlretrieve|Request)\s*\(|http\.(?:Get|Post|Do)\s*\("
    r"|navigator\.sendBeacon\s*\(|new\s+WebSocket\s*\(|https?\.request\s*\(",
    re.I,
)
NET_CONFIG = re.compile(r"(?:url|host|endpoint|base_url|baseurl|server|webhook|domain)\s*[:=]", re.I)


def domain_matches(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def comment_line(line):
    return line.lstrip().startswith(("//", "#", "/*", "*", "<!--"))


def destinations(line, whitelist):
    seen = set()
    for match in URL.finditer(line):
        url = match.group().rstrip(".,;")
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "dynamic").lower()
            if any(char in host for char in "${}%"):
                host = "dynamic"
            port = parsed.port or (443 if parsed.scheme in ("https", "wss") else 80)
        except ValueError:
            continue
        seen.add(host)
        yield {
            "content": url,
            "destination": host,
            "port": port,
            "protocol": parsed.scheme,
            "rule": "hardcoded_external_url",
            "severity": "LOW" if domain_matches(host, whitelist) else "HIGH",
        }
    if CLIENT.search(line) or NET_CONFIG.search(line):
        for match in BARE.finditer(line):
            host = match[1].lower()
            if host in seen:
                continue
            seen.add(host)
            yield {
                "content": match[0],
                "destination": host,
                "port": int(match[2]) if match[2] else None,
                "protocol": None,
                "rule": "bare_domain",
                "severity": "LOW" if domain_matches(host, whitelist) else "MEDIUM",
            }
    for match in IP.finditer(line):
        try:
            ip = ipaddress.ip_address(match.group())
        except ValueError:
            continue
        if ip.is_global and str(ip) not in seen:
            yield {
                "content": str(ip),
                "destination": str(ip),
                "port": None,
                "protocol": None,
                "rule": "public_ip",
                "severity": "MEDIUM",
            }
