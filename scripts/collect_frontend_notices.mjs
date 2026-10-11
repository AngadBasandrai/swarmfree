import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const output = path.join(root, 'dist', 'licenses');
fs.mkdirSync(output, { recursive: true });
const packages = new Map();
function visit(name, from) {
  let cursor = from;
  let directory;
  while (true) {
    const candidate = path.join(cursor, 'node_modules', name);
    if (fs.existsSync(path.join(candidate, 'package.json'))) { directory = candidate; break; }
    const parent = path.dirname(cursor);
    if (parent === cursor) return;
    cursor = parent;
  }
  if (packages.has(directory)) return;
  const metadata = JSON.parse(fs.readFileSync(path.join(directory, 'package.json'), 'utf8'));
  const notices = [];
  for (const file of fs.readdirSync(directory)) {
    if (/^(LICENSE|LICENCE|COPYING|OFL)/i.test(file) && fs.statSync(path.join(directory, file)).isFile()) {
      const filename = `${metadata.name.replaceAll('/', '__').replaceAll('@', '')}__${file}`;
      fs.copyFileSync(path.join(directory, file), path.join(output, filename));
      notices.push(filename);
    }
  }
  packages.set(directory, { name: metadata.name, version: metadata.version, license: metadata.license, notices });
  for (const dependency of Object.keys({ ...metadata.dependencies, ...metadata.optionalDependencies })) visit(dependency, directory);
}
const project = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
for (const name of Object.keys(project.dependencies)) visit(name, root);
fs.writeFileSync(path.join(output, 'DEPENDENCIES.json'), JSON.stringify([...packages.values()], null, 2));
fs.cpSync(output, path.join(root, 'build', 'notices', 'frontend'), { recursive: true });
console.log(`Included notices for ${packages.size} frontend packages.`);
