#!/usr/bin/env node
/**
 * probe.mjs — resolve the flags found statically against a live session.
 *
 * `mfr.py extract` tells you which GK ids and QEX keys the bundles read;
 * this asks the page what they currently evaluate to for the logged-in account.
 * Run it from two profiles (different accounts, countries, ages, device classes)
 * and diff the outputs — the flags that differ are the ones with a live
 * treatment/control split, which is what an experiment actually looks like.
 *
 * Usage:
 *   node probe.mjs --inventory snapshots/ig-2026-08-18/inventory.json \
 *                  --url https://www.instagram.com/ --profile ~/.mfr/ig \
 *                  --out probes/ig-account-a.json
 */
import { chromium } from 'playwright';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';

const args = Object.fromEntries(
  process.argv.slice(2).flatMap((a, i, all) =>
    a.startsWith('--') ? [[a.slice(2), all[i + 1]?.startsWith('--') === false ? all[i + 1] : true]] : []
  )
);
for (const req of ['inventory', 'url', 'profile', 'out']) {
  if (!args[req]) { console.error(`missing --${req}`); process.exit(2); }
}

const inv = JSON.parse(await readFile(args.inventory, 'utf8'));
const gkIds = [...new Set(Object.values(inv.modules).flatMap((m) => m.gkx))];
const qexKeys = [...new Set(Object.values(inv.modules).flatMap((m) => m.qex))];
process.stderr.write(`probing ${gkIds.length} GKs, ${qexKeys.length} QEX keys\n`);

const ctx = await chromium.launchPersistentContext(path.resolve(args.profile), {
  headless: args.headed ? false : true,
  viewport: { width: 1440, height: 900 },
});
const page = ctx.pages()[0] || (await ctx.newPage());
await page.goto(args.url, { waitUntil: 'networkidle', timeout: 60000 });

const result = await page.evaluate(([gkIds, qexKeys]) => {
  // The www/Polaris stack exposes the module registry as a global `require`.
  const req = window.require || window.__d?.require;
  if (typeof req !== 'function') return { error: 'no global require on this page' };
  const out = { gkx: {}, qex: {}, errors: [] };
  let gkx, qex;
  try { gkx = req('gkx'); } catch (e) { out.errors.push('gkx: ' + e.message); }
  try { qex = req('qex'); } catch (e) { out.errors.push('qex: ' + e.message); }
  for (const id of gkIds) {
    try { out.gkx[id] = gkx ? !!gkx(id) : null; } catch { out.gkx[id] = null; }
  }
  for (const k of qexKeys) {
    try { out.qex[k] = qex ? qex._(k) : null; } catch { out.qex[k] = null; }
  }
  return out;
}, [gkIds, qexKeys]);

await mkdir(path.dirname(path.resolve(args.out)), { recursive: true });
await writeFile(args.out, JSON.stringify({ url: args.url, probedAt: new Date().toISOString(), ...result }, null, 1));
const on = Object.values(result.gkx || {}).filter(Boolean).length;
process.stderr.write(`${args.out}: ${on}/${gkIds.length} gatekeepers on\n`);
if (result.error) process.stderr.write(`! ${result.error}\n`);
await ctx.close();
