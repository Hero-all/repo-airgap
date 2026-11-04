import ipaddress
import re
from urllib.parse import urlsplit

URL = re.compile(r"(?:https?|wss?)://[^\s\"'`<>\\)\]}]+", re.I)
BARE = re.compile(r"[\"']((?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,63})(?::(\d{1,5}))?(?:/[^\"']*)?[\"']")
IP = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
HTTP_METHODS = "get|post|put|patch|delete|head|options|request|stream"
CLIENT = re.compile(
    r"(?<![\w$])fetch\s*\("
    r"|(?<![\w$])axios(?:\.(?:" + HTTP_METHODS + r"|create)\s*\(|\s*\()"
    # Calls only: type hints (httpx.Client), exceptions (httpx.TimeoutException) and names
    # that merely end in "requests" (scmChangeRequests.) are not network calls.
    r"|(?<![\w$.])(?:requests|httpx|aiohttp)\.(?:" + HTTP_METHODS
    + r"|Client|AsyncClient|ClientSession|Session)\s*\("
    r"|(?<![\w$.])urllib\.(?:request\.)?(?:urlopen|urlretrieve|Request)\s*\(|(?<![\w$.])urlopen\s*\("
    r"|\bhttp\.(?:Get|Post|Do|NewRequest)\s*\("
    r"|navigator\.sendBeacon\s*\(|new\s+WebSocket\s*\(|(?<![\w$])https?\.(?:request|get)\s*\(",
)
NET_CONFIG = re.compile(r"(?:url|host|endpoint|base_url|baseurl|server|webhook|domain)\s*[:=]", re.I)
# URIs used as identifiers (XML namespaces, JSON Schema, spec links) that clients do not fetch.
IDENTIFIER_HOSTS = {
    "www.w3.org",
    "w3.org",
    "json-schema.org",
    "schemas.openxmlformats.org",
    "schemas.microsoft.com",
    "purl.org",
    "ns.adobe.com",
    "schemas.xmlsoap.org",
    "www.apache.org",
}


def domain_matches(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def is_local_host(host):
    """Loopback, private IPs and single-label names (e.g. Docker service `api`) are not internet egress."""
    if host == "dynamic":
        return False
    host = host.strip("[]").lower()
    if host in ("localhost", "0.0.0.0") or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return not ip.is_global
    except ValueError:
        return "." not in host


def comment_line(line):
    return line.lstrip().startswith(("//", "#", "/*", "*", "<!--"))


# Contexts where the browser or a client fetches the URL by itself.
FETCHED_CONTEXT = re.compile(
    r"(?i)\bsrc\s*[=:]|stylesheet|preload|prefetch|preconnect|dns-prefetch|rel=[\"']?(?:icon|manifest)"
    r"|<script|@import|url\(|<iframe|<img|<video|<audio|<source|importScripts|\.load\("
)
REFERENCE_CONTEXT = re.compile(r"(?i)\bhref\s*[=:]|<a\s|\blink\b|\bdocs?\b|documentation|learn more|\bhelp\b")
REFERENCE_PATH = re.compile(r"(?i)/(?:docs?|guide|wiki|blob|tree|issues|discussions|releases|pull|licen[cs]e)\b")


def is_reference_link(line, url):
    """A URL that is displayed or linked to, with no network call or fetching attribute on the line."""
    if NET_CONFIG.search(line) or FETCHED_CONTEXT.search(line):
        return False
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if host.startswith(("docs.", "doc.", "wiki.", "help.", "support.")):
        return True
    return bool(REFERENCE_CONTEXT.search(line) or REFERENCE_PATH.search(parsed.path or ""))


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
        before = line[: match.start()].rstrip()
        after = line[match.end() :]
        # Version strings (pkg==1.2.1.0, Chrome/130.0.0.0, >= 4.3.5.1) and CIDR ranges are not destinations.
        if before.endswith(("=", "<", ">", "~", "/", "v", "V", "@")) or re.match(r"/\d", after):
            continue
        if re.search(r"(?i)version|\bver\b|<path|\sd=[\"']|points=[\"']|viewBox", line):
            continue  # version text or inline SVG geometry (e.g. d="M3.138.75.75")
        if ip.is_global and str(ip) not in seen:
            yield {
                "content": str(ip),
                "destination": str(ip),
                "port": None,
                "protocol": None,
                "rule": "public_ip",
                "severity": "MEDIUM",
            }
