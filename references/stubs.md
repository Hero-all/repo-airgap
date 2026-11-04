# Caller-compatible stubs

These are examples, not universal replacements. Inspect the actual call sites.

```typescript
// Preserve the project's original function name, parameters, type and exports.
export async function checkUpdateAndAds() {
  return { hasUpdate: false, latestVersion: '0.0.0', ads: [], showSponsorModal: false };
}
```

```python
def send_usage_telemetry(payload: dict) -> dict:
    return {"status": "ok", "sanitized": True}
```

```jsx
export function SponsorModal() { return null; }
```

For Vue an optional render can become `<template><!-- sanitized --></template>`;
check script side effects and parent refs first. For Go preserve the exact
signature and construct typed defaults (for example `return Result{Ads: []Ad{}}, nil`
only if this is the real signature and callers accept it). Go automatic editing
is not supported in version 0.1.

A void analytics wrapper can become a no-op, but a wrapper returning a response
object needs that object's contract. Preserve async behavior. Neutralize imports
or initialization that would still send traffic when the module is loaded, using
their own reviewed candidates. Hiding a banner alone does not stop its effect
hook or data loader. Preserve error reporting if required by the user's business.
