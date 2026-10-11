import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const root=path.dirname(fileURLToPath(import.meta.url));
const records=[];
const read=file=>fs.readFileSync(file,'utf8');
const notices=(directory,explicit)=>{
 const files=fs.readdirSync(directory).filter(n=>/^(LICENSE|LICENCE|COPYING|NOTICE|OFL)/i.test(n)).map(n=>path.join(directory,n));
 if(explicit)files.push(path.resolve(directory,explicit));
 return [...new Set(files)].filter(f=>fs.statSync(f).isFile()).map(f=>({file:path.basename(f),text:read(f)}));
};
const cargo=JSON.parse(execFileSync('cargo',['metadata','--manifest-path',path.join(root,'src-tauri/Cargo.toml'),'--format-version','1','--locked'],{encoding:'utf8',maxBuffer:8*1024*1024,windowsHide:true}));
for(const p of cargo.packages)if(p.source)records.push({kind:'Rust',name:p.name,version:p.version,license:p.license,notices:notices(path.dirname(p.manifest_path),p.license_file)});
const seen=new Set();
function visit(name,from){
 let cursor=from,directory;
 for(;;){const candidate=path.join(cursor,'node_modules',name);if(fs.existsSync(path.join(candidate,'package.json'))){directory=candidate;break;}const parent=path.dirname(cursor);if(parent===cursor)throw Error('Dependency missing: '+name);cursor=parent;}
 if(seen.has(directory))return;seen.add(directory);const p=JSON.parse(read(path.join(directory,'package.json')));
 records.push({kind:'JavaScript',name:p.name,version:p.version,license:p.license,notices:notices(directory)});
 for(const dep of Object.keys(p.dependencies||{}))visit(dep,directory);
}
for(const name of Object.keys(JSON.parse(read(path.join(root,'package.json'))).dependencies))visit(name,root);
fs.writeFileSync(path.join(root,'notices.json'),JSON.stringify({projectLicense:read(path.join(root,'../LICENSE')),dependencies:records}));
console.log('Included Rust/JavaScript notices for '+records.length+' companion dependencies.');
