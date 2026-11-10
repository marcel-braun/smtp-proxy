import os
import subprocess
import sys
from pathlib import Path

from smtp_proxy.portable import idle_runtime


def test_lock_excludes_other_process_and_releases(tmp_path):
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from smtp_proxy.portable import runtime_status\n"
        "print(runtime_status(Path(sys.argv[1])))\n"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    def status_in_other_process():
        result = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)],
            env=environment, capture_output=True, text=True, check=True, timeout=10,
        )
        return result.stdout.strip()

    with idle_runtime(tmp_path):
        assert status_in_other_process() == "running"
    assert status_in_other_process() == "stopped"