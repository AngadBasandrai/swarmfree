import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests/ui', timeout: 45000, workers: 1, fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:8001', viewport: { width: 1440, height: 1000 }, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: { command: process.env.SWARM_TEST_PYTHON ? '"'+process.env.SWARM_TEST_PYTHON+'" scripts/serve_test.py' : process.platform === 'win32' ? '".venv\\Scripts\\python.exe" scripts/serve_test.py' : '.venv/bin/python scripts/serve_test.py', url: 'http://127.0.0.1:8001/api/health', reuseExistingServer: false, timeout: 30000 },
});
