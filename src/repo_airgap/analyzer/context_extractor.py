import re

from ..io import redact

FUNCTION = re.compile(
    r"(?:async\s+)?def\s+(\w+)|(?:async\s+)?function\s+(\w+)|"
    r"(?:const|let)\s+(\w+)\s*=.*=>|func\s+(?:\([^)]*\)\s*)?(\w+)"
)


def extract_context(lines, line_number, radius=15):
    start = max(1, line_number - radius)
    end = min(len(lines), line_number + radius)
    snippet = "\n".join(f"{i}: {redact(lines[i - 1])}" for i in range(start, end + 1))
    # Nearest preceding declaration is a locator hint, not a proven enclosing scope.
    function = None
    for line in reversed(lines[:line_number]):
        match = FUNCTION.search(line)
        if match:
            function = next(g for g in match.groups() if g)
            break
    return start, end, snippet, function
