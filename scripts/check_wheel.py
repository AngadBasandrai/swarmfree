"""Verify that a wheel serves its UI from outside the source tree."""
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


repo = Path(__file__).resolve().parent.parent
wheel = sorted((repo / "dist").glob("swarm_workbench-*.whl"))[-1]
with tempfile.TemporaryDirectory(prefix="swarm-wheel-check-") as directory:
    target = Path(directory)
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(target)
    code = """
import sys, tempfile
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from swarm.app import create_app
from fastapi.testclient import TestClient
import swarm
assert Path(swarm.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
with tempfile.TemporaryDirectory(prefix='swarm-data-') as data:
    with TestClient(create_app(data, token='test')) as client:
        response = client.get('/')
        assert response.status_code == 200 and '<title>Swarm' in response.text
        assert client.get('/api/health').json()['status'] == 'ok'
        assets = Path(swarm.__file__).parent / 'web_dist' / 'assets'
        name = next(assets.glob('*.js')).name
        assert client.get('/assets/' + name).status_code == 200
print('Wheel UI and API smoke check passed outside the source tree')
"""
    subprocess.run([sys.executable, "-I", "-c", code, str(target)], cwd=target, check=True)
