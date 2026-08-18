#!/usr/bin/env node
/**
 * collect.mjs — snapshot the shipped frontend of instagram.com / facebook.com.
 *
 * Drives a real Chromium over a list of routes and writes every JS bundle,
 * HTML document and GraphQL request/response into a snapshot directory.
 * Lazy chunks only load when their surface is visited, so the route list is
 * what determines coverage — extend it rather than the scroll depth.
 *
 * Usage:
 *   node collect.mjs --site instagram --out snapshots/ig-2026-08-18 --profile ~/.mfr/ig
 *   node collect.mjs --site facebook  --out snapshots/fb-2026-08-18 --profile ~/.mfr/fb --headed
 *
 * The first run should be --headed: log in by hand once, the persistent profile
 * keeps the session. Logged-out pages are a login wall and carry a small
 * fraction of the flags.
 */
import { chromium } from 'playwright';
import { createHash } from 'node:crypto';
import { mkdir, writeFile, appendFile } from 'node:fs/promises';
import path from 'node:path';

const ROUTES = {
  instagram: {
    origin: 'https://www.instagram.com',
    paths: [
      '/', '/reels/', '/explore/', '/explore/search/', '/direct/inbox/',
      '/your_activity/interactions/', '/accounts/edit/', '/accounts/meta_verified/',
      '/accounts/privacy_and_security/', '/create/style/', '/stories/',
      '/accounts/professional_account_settings/', '/settings/',
    ],
  },
  facebook: {
    origin: 'https://www.facebook.com',
    paths: [
      '/', '/watch/', '/reel/', '/marketplace/', '/groups/feed/', '/events/',
      '/messages/t/', '/ai/', '/settings', '/friends/', '/saved/',
      '/business/', '/gaming/', '/notifications/',
    ],
  },
};

const args = Object.fromEntries(
  process.argv.slice(2).flatMap((a, i, all) =>
    a.startsWith('--') ? [[a.slice(2), all[i + 1]?.startsWith('--') === false ? all[i + 1] : true]] : []
  )
);

const site = args.site;
if (!ROUTES[site]) {
  console.error('--site must be one of: ' + Object.keys(ROUTES).join(', '));
  process.exit(2);
}
const outDir = path.resolve(args.out || `snapshots/${site}-${new Date().toISOString().slice(0, 10)}`);
const profileDir = path.resolve(args.profile || `.mfr-profile-${site}`);
const { origin, paths } = ROUTES[site];

const jsDir = path.join(outDir, 'js');
const htmlDir = path.join(outDir, 'html');
await mkdir(jsDir, { recursive: true });
await mkdir(htmlDir, { recursive: true });

const manifestPath = path.join(outDir, 'manifest.jsonl');
const graphqlPath = path.join(outDir, 'graphql.jsonl');
const seen = new Set();

const ctx = await chromium.launchPersistentContext(profileDir, {
  headless: args.headed ? false : true,
  viewport: { width: 1440, height: 900 },
  args: ['--disable-blink-features=AutomationControlled'],
});
const page = ctx.pages()[0] || (await ctx.newPage());

page.on('response', async (res) => {
  const url = res.url();
  const ct = res.headers()['content-type'] || '';
  const isJs = /javascript|ecmascript/.test(ct) || /\.js(\?|$)/.test(url);
  if (!isJs) return;
  const key = createHash('sha1').update(url).digest('hex').slice(0, 16);
  if (seen.has(key)) return;
  seen.add(key);
  let body;
  try { body = await res.body(); } catch { return; }
  // Bundle basenames are content-hashed already; the url hash only breaks ties.
  const base = (url.split('?')[0].split('/').pop() || 'chunk') .replace(/[^\w.\-]/g, '_');
  const file = `${key}__${base}`.slice(0, 180);
  await writeFile(path.join(jsDir, file), body);
  await appendFile(manifestPath, JSON.stringify({ url, file, bytes: body.length }) + '\n');
});

page.on('request', async (req) => {
  if (!/\/api\/graphql|\/graphql\/?$/.test(req.url())) return;
  const post = req.postData();
  if (!post) return;
  const p = new URLSearchParams(post);
  // fb_api_req_friendly_name + doc_id name the query; variables carry the
  // resolved __relay_internal__pv__* provider flags for THIS account.
  await appendFile(graphqlPath, JSON.stringify({
    url: req.url(),
    name: p.get('fb_api_req_friendly_name'),
    doc_id: p.get('doc_id'),
    variables: p.get('variables'),
    server_timestamps: p.get('server_timestamps'),
  }) + '\n');
});

for (const p of paths) {
  const url = origin + p;
  process.stderr.write(`→ ${url}\n`);
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(4000);
    // Scroll to pull in the lazily-split chunks of infinite surfaces.
    for (let i = 0; i < 4; i++) {
      await page.mouse.wheel(0, 2500);
      await page.waitForTimeout(1500);
    }
    const html = await page.content();
    const name = (p.replace(/\W+/g, '_') || 'root') + '.html';
    await writeFile(path.join(htmlDir, name), html);
  } catch (e) {
    process.stderr.write(`  ! ${e.message}\n`);
  }
}

await writeFile(path.join(outDir, 'meta.json'), JSON.stringify({
  site, origin, paths, collectedAt: new Date().toISOString(), jsFiles: seen.size,
}, null, 2));

process.stderr.write(`\nsnapshot: ${outDir}  (${seen.size} js files)\n`);
await ctx.close();
