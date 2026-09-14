from pathlib import Path
import shutil
import subprocess
import sys


def test_scan_ignores_private_env_but_detects_tracked_env(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(Path(__file__).resolve().parents[1] / "scripts/secret_scan.py", scripts / "secret_scan.py")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".env\n")
    (tmp_path / ".env").write_text("ALPACA_PAPER_KEY=" + "A" * 25)

    def run():
        return subprocess.run([sys.executable, str(scripts / "secret_scan.py")], capture_output=True)

    assert run().returncode == 0
    subprocess.run(["git", "add", "-f", ".env"], cwd=tmp_path, check=True)
    result = run()
    assert result.returncode == 1 and b".env" in result.stdout
    assert ("A" * 25).encode() not in result.stdout
