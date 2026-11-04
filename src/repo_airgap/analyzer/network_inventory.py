def inventory(report):
    groups = {}
    for c in report["candidates"]:
        if not c.get("destination") and not c.get("sink_type"):
            continue
        destination = c.get("destination") or "dynamic"
        key = (destination, c.get("port"))
        group = groups.setdefault(key, {"destination": destination, "port": c.get("port"), "calls": []})
        group["calls"].append(
            {
                k: c.get(k)
                for k in (
                    "id",
                    "file_path",
                    "line_number",
                    "function",
                    "sink_type",
                    "protocol",
                    "decision",
                )
            }
        )
    return {
        "repo_path": report["repo_path"],
        "scan_timestamp": report["scan_timestamp"],
        "targets": list(groups.values()),
        "scope": "Static evidence only; declarations may not execute.",
    }
