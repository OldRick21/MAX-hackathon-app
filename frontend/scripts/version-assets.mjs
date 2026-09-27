import { createHash } from 'node:crypto';
import { readdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
async function collect(dir) {
  const result = [];
  for (const item of await readdir(dir, { withFileTypes: true })) {
    const file = path.join(dir, item.name);
    if (item.isDirectory()) result.push(...await collect(file));
    else if (/\.(js|css|html)$/.test(file)) result.push(file);
  }
  return result.sort();
}
const files = [...await collect(path.join(root, 'static')), ...await collect(path.join(root, 'templates'))].sort();
// Strip generated versions before hashing so repeated runs are idempotent.
const normalize = text => text.replace(/\?v=[a-f0-9]{12}(?=["'])/g, '');
const sources = await Promise.all(files.map(async file => [file, normalize(await readFile(file, 'utf8'))]));
const hash = createHash('sha256');
for (const [file, text] of sources) hash.update(path.relative(root, file)).update('\0').update(text).update('\0');
const version = hash.digest('hex').slice(0, 12);
for (const [file, source] of sources) {
  let output = source;
  if (file.endsWith('.js')) {
    output = output.replace(/(\bfrom\s*['"])(\.{1,2}\/[^'"?]+\.js)(['"])/g, `$1$2?v=${version}$3`);
  } else if (file.endsWith('.html')) {
    output = output.replace(/((?:src|href)=["'])(\/static\/[^'"?]+\.(?:js|css))(["'])/g, `$1$2?v=${version}$3`);
  }
  if (output !== await readFile(file, 'utf8')) await writeFile(file, output);
}
console.log(`Frontend asset version: ${version}`);
