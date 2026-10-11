"""Silent install/uninstall acceptance on disposable CI runners only.

Never run this against the developer's desktop. Native app/window launch remains
a separate manual check; only the installed console engine is exercised here.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('installer')
    args=parser.parse_args()
    if os.name!='nt' or os.getenv('CI','').casefold()!='true' or os.getenv('RUNNER_ENVIRONMENT')!='github-hosted':
        parser.error('This check is restricted to disposable GitHub-hosted Windows CI runners')
    runner=Path(os.environ['RUNNER_TEMP']).resolve()
    installer=Path(args.installer).resolve()
    if not installer.is_file():parser.error('Installer not found')
    with tempfile.TemporaryDirectory(prefix='swarm-install-acceptance-',dir=runner) as folder:
        destination=(Path(folder)/'Swarm Workbench').resolve()
        if not destination.is_relative_to(runner):raise ValueError('Install target escapes the disposable CI directory')
        subprocess.run([str(installer),'/S','/D='+str(destination)],check=True,timeout=180,creationflags=0x08000000)
        assert (destination/'swarm-workbench.exe').is_file()
        engine=destination/'swarm-engine.exe'
        assert engine.is_file() and (destination/'LICENSE').is_file()
        result=subprocess.run([str(engine),'--version'],capture_output=True,text=True,check=True,timeout=30,creationflags=0x08000000)
        assert 'Swarm Workbench engine' in result.stdout
        uninstallers=list(destination.glob('*ninstall*.exe'))
        assert len(uninstallers)==1,'Expected exactly one NSIS uninstaller'
        subprocess.run([str(uninstallers[0]),'/S'],check=True,timeout=120,creationflags=0x08000000)
        deadline=time.monotonic()+30
        while (destination/'swarm-workbench.exe').exists() and time.monotonic()<deadline:time.sleep(.2)
        assert not (destination/'swarm-workbench.exe').exists(),'Uninstaller left the application binary'
        print('Disposable Windows CI: silent install, installed engine and silent uninstall passed; no desktop app was launched')


if __name__=='__main__':main()
