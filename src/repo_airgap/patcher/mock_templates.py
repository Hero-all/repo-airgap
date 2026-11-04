"""Examples for Agent review, never guessed automatically from a URL."""

TEMPLATES = {
    "javascript": "export async function checkUpdateAndAds() {\n"
    "  return { hasUpdate: false, ads: [], showSponsorModal: false };\n}\n",
    "python": "def send_usage_telemetry(payload: dict) -> dict:\n"
    '    return {"status": "ok", "sanitized": True}\n',
    "react": "export function SponsorModal() { return null; }\n",
    "go": "// Preserve the exact signature and return typed zero/default values after reviewing callers.\n",
    "vue": "<template><!-- sanitized --></template>\n",
}
