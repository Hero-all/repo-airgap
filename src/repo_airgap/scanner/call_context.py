"""Per-file call context: import aliases, HTTP client instances, multi-line calls and URL constants.

Everything here is single-file and syntactic. It recognises common, explicit patterns; it does not
follow values across files, through function parameters, containers, or dynamic dispatch.
"""

import re

from .url_hunter import CLIENT, URL

# Python modules / JS packages whose call results are outbound network requests.
PY_HTTP_MODULES = {
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "urllib.request",
    "http.client",
    "websocket",
    "websockets",
}
JS_HTTP_PACKAGES = {
    "axios",
    "ky",
    "got",
    "node-fetch",
    "cross-fetch",
    "isomorphic-fetch",
    "undici",
    "superagent",
    "ofetch",
    "needle",
    "request",
}
# Constructors that return a reusable client/session object.
CONSTRUCTORS = (
    "Session|Client|AsyncClient|ClientSession|PoolManager|ProxyManager|HTTPConnection|HTTPSConnection"
    "|create|extend"
)
HTTP_METHODS = r"get|post|put|patch|delete|head|options|request|stream|fetch|ws_connect|urlopen"
# Package exports that are request functions when imported by name.
PY_FUNCTIONS = {"get", "post", "put", "patch", "delete", "head", "options", "request", "stream",
                "urlopen", "urlretrieve", "create_connection", "connect"}
PY_CLASSES = {"Session", "Client", "AsyncClient", "ClientSession", "PoolManager", "HTTPConnection",
              "HTTPSConnection", "Request"}
CODE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte")

PY_IMPORT = re.compile(r"^\s*import\s+([\w.]+)(?:\s+as\s+(\w+))?\s*$")
PY_FROM = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+\(?([\w\s,]+?)\)?\s*$")
JS_DEFAULT = re.compile(r"^\s*import\s+([\w$]+)\s*(?:,\s*\{[^}]*\})?\s+from\s+['\"]([^'\"]+)['\"]")
JS_STAR = re.compile(r"^\s*import\s+\*\s+as\s+([\w$]+)\s+from\s+['\"]([^'\"]+)['\"]")
JS_NAMED = re.compile(r"^\s*import\s+(?:[\w$]+\s*,\s*)?\{([^}]*)\}\s*from\s+['\"]([^'\"]+)['\"]")
JS_REQUIRE = re.compile(
    r"^\s*(?:const|let|var)\s+([\w$]+|\{[^}]*\})\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)"
)
CONSTANT = re.compile(
    r"^\s*(?:export\s+)?(?:const\s+|let\s+|var\s+|final\s+)?([A-Za-z_$][\w$]*)\s*(?::\s*[\w\[\]|<> .]+)?"
    r"=\s*f?[`'\"]((?:https?|wss?)://[^`'\"\s]+)[`'\"]\s*;?\s*(?:#.*|//.*)?$"
)
ASSIGN_TARGET = r"(?:(?:self|this)\.)?([A-Za-z_$][\w$]*)\s*(?::\s*[\w\[\]|<> .]+)?\s*=\s*(?:await\s+)?(?:new\s+)?"
XHR_ASSIGN = re.compile(ASSIGN_TARGET + r"XMLHttpRequest\s*\(")


def _js_package(spec):
    spec = spec.removeprefix("node:")
    return spec if spec in JS_HTTP_PACKAGES else None


def _names(spec):
    """`a, b as c` / `a as b` / `{ a, b: c }` -> [(exported, local)]."""
    pairs = []
    for part in spec.strip("{} ").split(","):
        part = part.strip()
        if not part or part.startswith("type "):
            continue
        m = re.match(r"([\w$]+)(?:\s+as\s+|\s*:\s*)([\w$]+)$", part)
        pairs.append((m[1], m[2]) if m else (part, part))
    return pairs


class FileContext:
    """Aliases and instances seen in one file, compiled into call patterns."""

    def __init__(self, lines, suffix):
        self.modules = set()  # local names bound to an HTTP module/package object
        self.functions = set()  # local names bound to a request function (callable directly)
        self.classes = set()  # local names bound to a client class
        self.instances = set()  # variables/attributes holding a client instance
        self.xhr = set()  # variables holding an XMLHttpRequest
        self.constants = {}  # NAME -> literal URL assigned in this file
        self.enabled = suffix.lower() in CODE_SUFFIXES
        if not self.enabled:
            return
        ambiguous = set()
        for line in lines:
            self._imports(line)
            m = CONSTANT.match(line)
            if m and self.constants.setdefault(m[1], m[2]) != m[2]:
                ambiguous.add(m[1])  # reassigned to another URL: do not guess which one is used
        for name in ambiguous:
            del self.constants[name]
        ctor = self._constructor_pattern()
        assign = re.compile(ASSIGN_TARGET + ctor)
        with_as = re.compile(r"\bwith\s+(?:await\s+)?" + ctor + r"[^\n]*?\bas\s+(\w+)")
        keywords = ("Session", "Client", "create", "extend", "PoolManager", "ProxyManager", "Connection",
                    *self.classes)
        for line in lines:
            if "XMLHttpRequest" in line:
                m = XHR_ASSIGN.search(line)
                if m:
                    self.xhr.add(m[1])
            # Cheap prefilter: the constructor patterns are costly to try at every position.
            if "(" not in line or not any(k in line for k in keywords):
                continue
            for m in assign.finditer(line):
                self.instances.add(m[1])
            for m in with_as.finditer(line):
                self.instances.add(m[m.lastindex])
        self._compile()

    def _imports(self, line):
        m = PY_IMPORT.match(line)
        if m and m[1] in PY_HTTP_MODULES:
            if m[2]:
                self.modules.add(m[2])  # import requests as rq
            elif "." not in m[1]:
                self.modules.add(m[1])
            return
        m = PY_FROM.match(line)
        if m:
            module, names = m[1], _names(m[2])
            if module in PY_HTTP_MODULES:
                for exported, local in names:
                    if exported in PY_FUNCTIONS:
                        self.functions.add(local)
                    elif exported in PY_CLASSES:
                        self.classes.add(local)
            elif module == "urllib":
                self.modules.update(local for exported, local in names if exported == "request")
            return
        for pattern in (JS_DEFAULT, JS_STAR):
            m = pattern.match(line)
            if m and _js_package(m[2]):
                self.modules.add(m[1])
                self.functions.add(m[1])  # ky(url), got(url), axios(config), fetch(url)
        m = JS_NAMED.match(line)
        if m and _js_package(m[2]):
            for exported, local in _names(m[1]):
                if exported in ("default", "fetch", "request", "get", "post", "stream", "ofetch", "$fetch"):
                    self.functions.add(local)
                elif exported == "Client":  # undici Agent/Pool are dispatchers, not requests
                    self.classes.add(local)
        m = JS_REQUIRE.match(line)
        if m and _js_package(m[2]):
            if m[1].startswith("{"):
                for exported, local in _names(m[1]):
                    if exported in ("fetch", "request", "get", "post", "stream"):
                        self.functions.add(local)
            else:
                self.modules.add(m[1])
                self.functions.add(m[1])

    def _constructor_pattern(self):
        alts = []
        modules = self.modules | {"requests", "httpx", "aiohttp", "urllib3", "axios", "ky", "got"}
        alts.append(r"(?:" + "|".join(map(re.escape, sorted(modules))) + r")\.(?:" + CONSTRUCTORS + r")\s*\(")
        alts.append(r"http\.client\.HTTPS?Connection\s*\(")
        if self.classes:
            alts.append(r"(?<![\w$.])(?:" + "|".join(map(re.escape, sorted(self.classes))) + r")\s*\(")
        return "(?:" + "|".join(alts) + ")"

    def _compile(self):
        alts = []
        # Builtin module names are already covered by CLIENT; aliases add new spellings.
        if self.modules:
            names = "|".join(map(re.escape, sorted(self.modules)))
            alts.append(r"(?<![\w$.])(?:" + names + r")\.(?:" + HTTP_METHODS + r"|" + CONSTRUCTORS + r")\s*\(")
        if self.functions:
            alts.append(r"(?<![\w$.])(?:" + "|".join(map(re.escape, sorted(self.functions))) + r")\s*\(")
        if self.classes:
            alts.append(r"(?<![\w$.])(?:new\s+)?(?:" + "|".join(map(re.escape, sorted(self.classes))) + r")\s*\(")
        self.alias_call = re.compile("|".join(alts)) if alts else None
        receivers = []
        if self.instances:
            names = "|".join(map(re.escape, sorted(self.instances)))
            receivers.append(r"(?:" + names + r")\.(?:" + HTTP_METHODS + r")\s*\(")
        if self.xhr:
            receivers.append(r"(?:" + "|".join(map(re.escape, sorted(self.xhr))) + r")\.open\s*\(")
        self.instance_call = (
            re.compile(r"(?:(?<=\bself\.)|(?<=\bthis\.)|(?<![\w$.]))(?:" + "|".join(receivers) + ")")
            if receivers
            else None
        )

    def call_kind(self, line):
        """'import_alias' / 'client_instance' when the line calls through a tracked name, else None."""
        if not self.enabled:
            return None
        if self.alias_call and self.alias_call.search(line) and not CLIENT.search(line):
            return "import_alias"
        if self.instance_call and self.instance_call.search(line):
            return "client_instance"
        return None


def call_span(lines, n, limit=8):
    """Text of the call starting on 1-based line n, following unbalanced parentheses for `limit` lines.

    Only parentheses count; braces would run on into a function body, e.g. `fetch(req, env) {`.
    """
    depth, parts = 0, []
    for line in lines[n - 1 : n - 1 + limit]:
        parts.append(line)
        quote = None
        for char in line:
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'`":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
        if depth <= 0:
            break
    return parts


ENV_ARG = re.compile(
    r"\(\s*(?:os\.environ\.get\(\s*|os\.getenv\(\s*|os\.environ\[\s*|process\.env\[\s*)['\"]([A-Za-z_]\w*)['\"]"
    r"|\(\s*(?:process\.env|import\.meta\.env)\.([A-Za-z_]\w*)"
)
SETTINGS_ARG = re.compile(
    r"\(\s*(?:(?:self|this)\.)?(?:settings|config|conf|cfg|options|opts|env|app\.config|current_app\.config)"
    r"(?:\.|\[\s*['\"])([A-Za-z_]\w*)"
)
IDENTIFIER_ARG = re.compile(r"\(\s*(?:(?:self|this)\.)?([A-Za-z_$][\w$]*)\s*(?:[,)+]|$)")
# Client constructors configured with a base URL: axios.create({ baseURL: process.env.API_BASE_URL }).
BASE_URL_KW = re.compile(
    r"\bbase_?url\s*[:=]\s*(?:process\.env\.|import\.meta\.env\.|(?:self\.|this\.)?(?:settings|config)\.)"
    r"([A-Za-z_]\w*)"
    r"|\bbase_?url\s*[:=]\s*(?:os\.environ\.get\(|os\.getenv\(|os\.environ\[)\s*['\"](\w+)['\"]",
    re.I,
)
XHR_OPEN = re.compile(r"\.open\(\s*['\"][A-Za-z]+['\"]\s*,\s*")


def resolve_argument(code, constants, config_name):
    """Destination for a call whose URL argument is a name: (destination, signal) or (None, None).

    Literal constants assigned in the same file resolve to their URL. Environment reads, settings
    attributes and names that look like configuration (API_BASE_URL, self.endpoint) become
    `config:<NAME>`. Anything else, including plain local variables, stays unresolved.
    """
    code = XHR_OPEN.sub(".open(", code)
    m = ENV_ARG.search(code)
    if m:
        return "config:" + (m[1] or m[2]), "environment_config"
    m = SETTINGS_ARG.search(code)
    if m and config_name.search(m[1]):
        return "config:" + m[1], "settings_reference"
    m = IDENTIFIER_ARG.search(code)
    if m:
        name = m[1]
        if name in constants and URL.match(constants[name]):
            return constants[name], "resolved_constant"
        if config_name.search(name):
            return "config:" + name, "config_constant"
    m = BASE_URL_KW.search(code)
    if m:
        return "config:" + (m[1] or m[2]), "environment_config"
    return None, None
