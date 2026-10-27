// Post-build CSP-construction gate (UI-M2 design (a)).
//
// The built index.html must contain no `src=`-less <script>, no `style=`
// attribute, and must reference assets under the `/ui/` base — UI-M3's
// strict `default-src 'self'` CSP depends on exactly this. Runs
// automatically after every `npm run build` via the `postbuild` script
// and is therefore part of `make ui-build` / `make ui-check` / `make check`.
import { readFileSync } from 'node:fs';

let html;
try {
  html = readFileSync(new URL('../dist/index.html', import.meta.url), 'utf8');
} catch {
  console.error('✗ ui/dist/index.html not found — run the build first (npm run build).');
  process.exit(1);
}

const problems = [];
// `\ssrc=` (not just `src=`) so attribute-like text such as `data-src=`
// cannot satisfy the lookahead.
if (/<script(?![^>]*\ssrc=)/.test(html)) {
  problems.push('inline <script> without a src attribute');
}
if (/style=/.test(html)) {
  problems.push('style= attribute present');
}
if (!html.includes('/ui/assets/')) {
  problems.push('missing /ui/assets/ base prefix');
}
if (problems.length > 0) {
  console.error(`✗ CSP-construction check failed: ${problems.join('; ')}`);
  process.exit(1);
}
console.log('✓ CSP-construction check passed (external scripts only, no style=, base /ui/)');
