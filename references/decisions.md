# Reviewed decisions format

Use the candidate ID and exact relative POSIX file path from the current scan.
Every review requires a reason and reviewer. Source edits are explicit exact
replacements; an `old` value must occur once and include its candidate evidence
line. Multiple edits in one file must not overlap. Keep/core/legal candidates
within an edited line range block the plan. The CLI does not prove semantic
equivalence and does not silently approve an entire file.

```json
{
  "reviews": [
    {
      "candidate_id": "CAND-00001",
      "decision": "MOCK_SILENT",
      "reason": "Optional heartbeat only; callers expect an ok result.",
      "reviewer": "local-review"
    }
  ],
  "edits": [
    {
      "candidate_id": "CAND-00001",
      "file_path": "src/utils/telemetry.py",
      "old": "    return send_to_server('https://telemetry.example/collect', payload)",
      "new": "    return {\"ok\": True}"
    }
  ]
}
```

`plan` checks every candidate file hash, even retained core files. Existing user
modifications are the baseline: rollback restores the exact bytes scanned, not
Git HEAD. Edits to files without candidates are currently manual. The plan
contains Base64 before/after snapshots and a reviewable diff; Base64 is storage,
not encryption. A plan is not a signed or sandboxed security boundary. Inspect
plans received from other people before applying them.

Before preparing this file, run `repo-airgap review TARGET`. Its
`.airgap/review_inventory.json` is the user-facing list of inferred destinations
and uses. Save choices with `--set ITEM-001=DISABLE`, `KEEP`, `REVIEW`, or (for a
Google authentication item) `REPLACE_LOCAL_AUTH`. Unspecified items are persisted
as `KEEP`; they are not eligible for an edit. The candidate-level `decision` in
this file explains the exact edit after the user has selected the corresponding
inventory item.

Newlines and BOMs are preserved in snapshots. Match CRLF with `\r\n` where needed;
do not normalize an entire file to make a snippet match. Read-only scan outputs
are under `.airgap` by default. `--output` can export the JSON to an external path;
within the target it must remain under `.airgap`.
