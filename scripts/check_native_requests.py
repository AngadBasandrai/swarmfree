"""Check the native attachment boundary without opening a desktop window."""
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    source = Path(__file__).resolve().parent.parent / "src-tauri" / "src" / "request_limits.rs"
    flags = 0x08000000 if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="swarm-native-request-check-") as folder:
        binary = Path(folder) / ("checks.exe" if os.name == "nt" else "checks")
        subprocess.run(["rustc", "--test", str(source), "-o", str(binary)], check=True, creationflags=flags)
        result = subprocess.run([str(binary)], check=True, capture_output=True, text=True, creationflags=flags)
        print(result.stdout)


if __name__ == "__main__":
    main()
