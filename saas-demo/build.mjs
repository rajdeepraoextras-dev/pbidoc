import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';

const html = await readFile(new URL('./index.html', import.meta.url), 'utf8');
const css = await readFile(new URL('./styles.css', import.meta.url), 'utf8');
const js = await readFile(new URL('./script.js', import.meta.url), 'utf8');
const og = await readFile(new URL('./og.png', import.meta.url));
const robots = await readFile(new URL('./robots.txt', import.meta.url), 'utf8');
const sitemap = await readFile(new URL('./sitemap.xml', import.meta.url), 'utf8');

const worker = `
const HTML = ${JSON.stringify(html)};
const CSS = ${JSON.stringify(css)};
const SCRIPT = ${JSON.stringify(js)};
const OG_BASE64 = ${JSON.stringify(og.toString('base64'))};
const ROBOTS = ${JSON.stringify(robots)};
const SITEMAP = ${JSON.stringify(sitemap)};

function decodeBase64(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

export default {
  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === '/styles.css') return new Response(CSS, { headers: { 'content-type': 'text/css; charset=utf-8', 'cache-control': 'public, max-age=3600' } });
    if (path === '/script.js') return new Response(SCRIPT, { headers: { 'content-type': 'text/javascript; charset=utf-8', 'cache-control': 'public, max-age=3600' } });
    if (path === '/og.png') return new Response(decodeBase64(OG_BASE64), { headers: { 'content-type': 'image/png', 'cache-control': 'public, max-age=86400' } });
    if (path === '/robots.txt') return new Response(ROBOTS, { headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'public, max-age=3600' } });
    if (path === '/sitemap.xml') return new Response(SITEMAP, { headers: { 'content-type': 'application/xml; charset=utf-8', 'cache-control': 'public, max-age=3600' } });
    return new Response(HTML, { headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-cache' } });
  }
};
`;

await rm(new URL('./dist', import.meta.url), { recursive: true, force: true });
await mkdir(new URL('./dist/server', import.meta.url), { recursive: true });
await writeFile(new URL('./dist/server/index.js', import.meta.url), worker);
