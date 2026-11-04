import math
import re
from collections import Counter

from .url_hunter import CLIENT

SINKS = [
    ("http_client", CLIENT),
    ("socket", re.compile(r"socket\.(?:socket|create_connection)|net\.connect|Socket\s*\(")),
    ("dns", re.compile(r"(?:dns\.(?:resolve\w*|lookup)|socket\.getaddrinfo|dns\.resolver)\s*\(")),
    (
        "shell_upload",
        re.compile(r"\b(?:curl|wget)\b.*(?:--data|--upload-file|--post-data|--post-file|\s-[dTF]\s)"),
    ),
    (
        "cloud_upload",
        re.compile(r"\.upload_file\s*\(|\.put_object\s*\(|\.upload_blob\s*\(|PutObjectCommand\s*\("),
    ),
    ("ftp_sftp", re.compile(r"ftplib\.|\.putfo\s*\(|SFTPClient|paramiko\.Transport")),
    ("smtp", re.compile(r"smtplib\.|\.sendmail\s*\(|\.sendMail\s*\(")),
]


def network_sinks(line):
    return [name for name, pattern in SINKS if pattern.search(line)]


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
