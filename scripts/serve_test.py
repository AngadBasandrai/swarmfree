import tempfile
import os
import uvicorn
from contextlib import asynccontextmanager
from swarm.app import create_app
from test_model_fixture import install_model_fixture

if __name__ == "__main__":
    os.environ["SWARM_PUBLIC_ORIGIN"] = "http://127.0.0.1:8001"
    os.environ["SWARM_OLLAMA_URL"] = "http://127.0.0.1:1"
    with tempfile.TemporaryDirectory(prefix="swarm-ui-") as directory:
        app = create_app(directory, token="ui-test-owner")
        original_lifespan = app.router.lifespan_context
        @asynccontextmanager
        async def fixture_lifespan(app):
            async with original_lifespan(app):
                install_model_fixture(app.state.store, app.state.providers.vault)
                yield
        app.router.lifespan_context = fixture_lifespan
        uvicorn.run(app, host="127.0.0.1", port=8001, access_log=False)
