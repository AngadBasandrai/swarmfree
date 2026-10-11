"""Gate publication of already-built artifacts against source and acceptance CI."""
import hashlib
import json
from pathlib import Path
import subprocess

REPO = 'AngadBasandrai/swarmfree'
TARGETS = {'darwin-aarch64', 'darwin-x86_64', 'linux-x86_64'}

def read_run(id):
    if not str(id).isdigit(): raise ValueError('Invalid workflow run ID')
    command = ['gh', 'run', 'view', str(id), '--repo', REPO, '--json', 'name,headSha,status,conclusion,jobs']
    return json.loads(subprocess.check_output(command, text=True))

def verify_existing(root, tag, build_run, acceptance_run):
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    tagged = subprocess.check_output(['git', 'rev-parse', tag+'^{commit}'], cwd=root, text=True).strip()
    if source != tagged: raise ValueError('Publication must use the unchanged tagged source checkout')
    builds = read_run(build_run)
    if builds['name'] != 'Desktop artifacts' or builds['headSha'] != source or builds['status'] != 'completed':
        raise ValueError('Build evidence does not match tagged source')
    for target in TARGETS:
        jobs = [j for j in builds['jobs'] if target in j['name']]
        if len(jobs) != 1 or jobs[0]['conclusion'] != 'success': raise ValueError('Platform build did not pass: '+target)
    windows = next(j for j in builds['jobs'] if 'windows-x86_64' in j['name'])
    failed = [s['name'] for s in windows['steps'] if s['conclusion'] == 'failure']
    if failed not in ([], ['Disposable Windows installation acceptance']): raise ValueError('Unresolved Windows build failure')
    if not any(s['name'] == 'Verify frozen engine and build platform manifest' and s['conclusion'] == 'success' for s in windows['steps']):
        raise ValueError('Windows frozen engine/signature checks did not pass')
    acceptance = read_run(acceptance_run)
    if acceptance['name'] != 'Signed Windows installer acceptance' or acceptance['conclusion'] != 'success':
        raise ValueError('Windows installation acceptance did not pass')
    proof = json.loads((root/'build/acceptance/accepted.json').read_text(encoding='utf-8-sig'))
    if proof['tag'] != tag or proof['source_commit'] != source or proof.get('installation_passed') is not True:
        raise ValueError('Installation proof does not match tagged source')
    binary = root/'build/collected/windows-x86_64'/('Swarm-Workbench_'+tag[1:]+'_x64-setup.exe')
    with binary.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if proof['sha256'] != digest or proof.get('publisher_signature') is not True:
        raise ValueError('Installer bytes differ from the accepted artifact')
    return dict(source_tag=tag, source_commit=source, platform_build_run=int(build_run),
                windows_install_acceptance_run=int(acceptance_run), windows_installer_sha256=digest,
                windows_build='Publisher local build of the matching tagged app source; independently checked in disposable CI',
                repaired_check='NSIS unquoted final /D directory override', updater_signatures=True)
