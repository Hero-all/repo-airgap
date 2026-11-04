import assert from 'node:assert/strict';

// Process-level regression guard: any attempted fetch fails before real I/O.
// This is not OS-level network isolation and does not cover sockets or DNS.
const attempts = [];
globalThis.fetch = async function blockedFetch(url) {
  attempts.push(String(url));
  throw new Error('network-disabled-fixture');
};
const { checkUpdateAndAds } = await import('./src/utils/telemetry.ts');
const { askModel } = await import('./src/api/openai.ts');
assert.deepEqual(await checkUpdateAndAds(), {
  hasUpdate: false, latestVersion: '0.0.0', ads: [], showSponsorModal: false
});
assert.deepEqual(attempts, []);
await assert.rejects(() => askModel('hello'), /network-disabled-fixture/);
assert.deepEqual(attempts, ['https://api.openai.com/v1/chat/completions']);
console.log('PASS: optional request removed; core API retained; return contract preserved.');
