import math
import re
from collections import Counter

from .url_hunter import CLIENT

SINKS = [
    ("http_client", CLIENT),
    (
        "socket",
        re.compile(
            r"\bsocket\.(?:socket|create_connection)\s*\(|\bnet\.(?:connect|createConnection|Socket)\s*\("
            r"|(?<![\w$.])(?:new\s+)?Socket\s*\("
        ),
    ),
    (
        "dns",
        re.compile(r"(?<![\w$])(?:dns\.(?:resolve\w*|lookup)|socket\.getaddrinfo|dns\.resolver\.\w+)\s*\("),
    ),
    (
        "shell_upload",
        re.compile(r"\b(?:curl|wget)\b.*(?:--data|--upload-file|--post-data|--post-file|\s-[dTF]\s)"),
    ),
    (
        "cloud_upload",
        # boto3/azure/gcs receivers only; application methods named upload_file are not cloud egress.
        re.compile(
            r"(?i)\b\w*(?:s3|bucket|boto|blob|storage)\w*\.(?:upload_file|upload_fileobj|put_object|upload_blob)\s*\("
            r"|PutObjectCommand\s*\("
        ),
    ),
    ("ftp_sftp", re.compile(r"ftplib\.|\.putfo\s*\(|SFTPClient|paramiko\.Transport")),
    ("smtp", re.compile(r"smtplib\.|\.sendmail\s*\(|\.sendMail\s*\(")),
]


def network_sinks(line):
    return [name for name, pattern in SINKS if pattern.search(line)]


# fetch('/api/x'), axios.get(`/v1/...`): same-origin call to the application's own backend.
SELF_RELATIVE = re.compile(
    r"(?:\bfetch|\baxios(?:\.\w+)?|\.(?:get|post|put|patch|delete|request))\s*\(\s*[`'\"]/(?![/`'\"])"
)
# fetch(`${WEBUI_API_BASE_URL}/x`), requests.get(f"{self.base_url}/x"): deployer-configured backend.
CONFIG_TEMPLATE = re.compile(r"\(\s*f?[`'\"]\s*\$?\{\s*([A-Za-z_$][\w.$]*)\s*\}")
CONFIG_NAME = re.compile(
    r"(?i)(?:base_?url|api_?url|server_?url|service_?url|api_?base|base_?path|endpoint|_url|_host|origin)$"
)


def dynamic_destination(code):
    """Best-effort label for a call without a literal host: self, config:<NAME>, or dynamic."""
    if SELF_RELATIVE.search(code):
        return "self"
    match = CONFIG_TEMPLATE.search(code)
    if match:
        name = match[1].split(".")[-1]
        if CONFIG_NAME.search(name):
            return "config:" + name
    return "dynamic"


def related_signals(context):
    signals = []
    for name, regex in [
        ("file_read_nearby", r"readFile|read_text|read_bytes|open\s*\("),
        ("machine_identifier_nearby", r"hostname|machine.?id|fingerprint|platform\."),
        ("environment_read_nearby", r"process\.env|os\.environ|os\.getenv|os\.Getenv"),
        ("credential_reference_nearby", r"(?i)api[_-]?key|password|secret|access_token"),
    ]:
        if re.search(regex, context):
            signals.append(name)
    for token in re.findall(r"[\"']([a-zA-Z0-9+/=_-]{24,120})[\"']", context):
        counts = Counter(token)
        entropy = -sum((c / len(token)) * math.log2(c / len(token)) for c in counts.values())
        if entropy >= 4:
            signals.append("high_entropy_string_nearby")
            break
    return signals
