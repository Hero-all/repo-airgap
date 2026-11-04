"""Conservative signature checks, not a replacement for project compilation."""

import ast
import re
from pathlib import Path


def _python_signatures(text):
    found = {}

    def visit(body, prefix=""):
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, prefix + node.name + ".")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[prefix + node.name] = (
                    type(node).__name__,
                    ast.dump(node.args),
                    ast.dump(node.returns) if node.returns else None,
                    tuple(ast.dump(d) for d in node.decorator_list),
                )

    visit(ast.parse(text.lstrip("\ufeff")).body)
    return found


def _js_signatures(text):
    # Covers declarations and exported arrow bindings, not all JS syntax.
    # Retain the matched signature text, including export and async modifiers.
    declaration = re.compile(
        r"(?m)^[ \t]*((?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s+"
        r"([A-Za-z_$][\w$]*)\s*(?:<[^>\n]+>)?\s*\([^)]*\)(?:\s*:[^{;\n]+)?)\s*\{"
    )
    arrow = re.compile(
        r"(?m)^[ \t]*(export\s+(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
        r"(?:\([^)]*\)|[\w$]+)(?:\s*:[^=\n]+)?\s*=>)"
    )
    return {
        m[2]: re.sub(r"\s+", " ", m[1]).strip()
        for pattern in (declaration, arrow)
        for m in pattern.finditer(text)
    }


def preserve_signatures(filename, before, after):
    suffix = Path(filename).suffix.lower()
    if suffix == ".py":
        original, updated = _python_signatures(before), _python_signatures(after)
    elif suffix in (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"):
        original, updated = _js_signatures(before), _js_signatures(after)
    else:
        return
    for name, signature in original.items():
        if updated.get(name) != signature:
            raise ValueError(f"Function signature/export changed: {filename}:{name}")
