#!/usr/bin/env node
// Collects every JS bundle Meta's web frontends serve, plus the HTML bootstrap
// payloads that carry gatekeeper values. Feed the output dir to extract.py.
//
//   node collect.mjs --site ig --out out/ig
//   node collect.mjs --site fb --out out/fb --state fb-state.json
//
// --state points at a Playwright storageState file. Logged-out collection works
// but sees far fewer lazy chunks; log in once with --login to produce one.

import { chromium } from 'playwright-core';
import { mkdir, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';

const EXECUTABLES = [
  '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  '/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell',
];

const SITES = {
  ig: {
    origin: 'https://www.instagram.com',
    routes: ['/', '/reels/', '/explore/', '/direct/inbox/', '/accounts/edit/'],
    // Ships the readable quick-experiment map when it is reachable.
    probes: ['/data/shared_data/', '/api/v1/web/data/shared_data/'],
  },
  fb: {
    origin: 'https://www.facebook.com',
    routes: ['/', '/watch/', '/marketplace/', '/groups/feed/', '/settings/'],
    probes: [],
  },
};

function arg(name, fallback = null) {
  const i = process.argv.indexOf(`--${name}`);
  if (i === -1) return fallback;
  const next = process.argv[i + 1];
  return !next || next.startsWith('--') ? true : next;
}

function slug(url) {
  const u = new URL(url);
  const base = path.basename(u.pathname) || 'index';
  const hash = createHash('sha1').update(url).digest('hex').slice(0, 10);
  return `${base.replace(/[^\w.-]/g, '_')}.${hash}.js`;
}

const siteKey = arg('site', 'ig');
const site = SITES[siteKey];
if (!site) throw new Error(`unknown --site ${siteKey}; expected ig or fb`);

const outDir = arg('out', `out/${siteKey}`);
const statePath = arg('state');
const wantLogin = arg('login') === true;

await mkdir(path.join(outDir, 'js'), { recursive: true });
await mkdir(path.join(outDir, 'html'), { recursive: true });

const executablePath = EXECUTABLES.find((p) => existsSync(p));
const browser = await chromium.launch({
  headless: !wantLogin,
  ...(executablePath ? { executablePath } : {}),
});

const context = await browser.newContext({
  storageState: statePath && existsSync(statePath) ? statePath : undefined,
  viewport: { width: 1440, height: 900 },
  userAgent:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
    '(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
  locale: 'en-US',
});

const manifest = [];
const seen = new Set();

context.on('response', async (response) => {
  const url = response.url();
  const type = response.headers()['content-type'] || '';
  const looksLikeJs = /javascript|ecmascript/.test(type) || /\.js(\?|$)/.test(url);
  if (!looksLikeJs || seen.has(url)) return;
  seen.add(url);
  try {
    const body = await response.body();
    const name = slug(url);
    await writeFile(path.join(outDir, 'js', name), body);
    manifest.push({ url, file: `js/${name}`, bytes: body.length, status: response.status() });
    process.stdout.write(`\r  bundles: ${manifest.length}   `);
  } catch {
    /* body already discarded by the browser — nothing to recover */
  }
});

const page = await context.newPage();

if (wantLogin) {
  console.log(`Log in manually, then press Enter here.`);
  await page.goto(site.origin, { waitUntil: 'domcontentloaded' });
  await new Promise((resolve) => process.stdin.once('data', resolve));
  if (statePath) await context.storageState({ path: statePath });
}

for (const route of site.routes) {
  const url = site.origin + route;
  console.log(`\n→ ${url}`);
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    // Lazy chunks land on idle and on scroll; give both a chance.
    await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {});
    for (let i = 0; i < 4; i++) {
      await page.mouse.wheel(0, 2000);
      await page.waitForTimeout(1200);
    }
    const html = await page.content();
    await writeFile(
      path.join(outDir, 'html', `${route.replace(/\W+/g, '_') || 'root'}.html`),
      html,
    );
  } catch (err) {
    console.log(`  skipped: ${err.message.split('\n')[0]}`);
  }
}

for (const probe of site.probes) {
  const url = site.origin + probe;
  try {
    const res = await context.request.get(url, {
      headers: { 'x-requested-with': 'XMLHttpRequest' },
    });
    const body = await res.text();
    await writeFile(path.join(outDir, `probe${probe.replace(/\W+/g, '_')}.json`), body);
    console.log(`\n→ probe ${probe}: ${res.status()} (${body.length} bytes)`);
  } catch (err) {
    console.log(`\n→ probe ${probe} failed: ${err.message.split('\n')[0]}`);
  }
}

await writeFile(path.join(outDir, 'manifest.json'), JSON.stringify(manifest, null, 2));
console.log(`\n\nSaved ${manifest.length} bundles to ${outDir}`);
await browser.close();
