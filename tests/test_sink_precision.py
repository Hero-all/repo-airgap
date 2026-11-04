from repo_airgap.engine import scan


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sinks(root):
    return {(c.file_path, c.line_number) for c in scan(root).candidates if c.category == "NETWORK_SINK"}


def test_non_call_lookalikes_are_not_sinks(tmp_path):
    # Each line is a real false positive seen in dify / lobe-chat.
    write(
        tmp_path,
        "a.py",
        "def __init__(self, client: httpx.Client) -> None:\n"
        "except httpx.TimeoutException as e:\n"
        "upload_file = FileService(db.engine).upload_file(name)\n"
        "r = httpx.get(url, timeout=httpx.Timeout(10.0))\n"
        "s3.upload_file('a', 'bucket', 'key')\n",
    )
    write(
        tmp_path,
        "b.ts",
        "export type NewScm = typeof scmChangeRequests.$inferInsert;\n"
        "function PatchedWebSocket(this: WebSocket, url: string) {}\n"
        "return this.getActiveSocket()?.connected === true\n"
        "const ws = new WebSocket(url)\n",
    )
    found = sinks(tmp_path)
    assert {n for f, n in found if f == "a.py"} == {4, 5}
    assert {n for f, n in found if f == "b.ts"} == {4}
