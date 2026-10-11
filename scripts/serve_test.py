import tempfile
import os
import uvicorn
from swarm.app import create_app

if __name__ == "__main__":
    os.environ["SWARM_PUBLIC_ORIGIN"] = "http://127.0.0.1:8001"
    with tempfile.TemporaryDirectory(prefix="swarm-ui-") as directory:
        uvicorn.run(create_app(directory, token="ui-test-owner"), host="127.0.0.1", port=8001, access_log=False)
