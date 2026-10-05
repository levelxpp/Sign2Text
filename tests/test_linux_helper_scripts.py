from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
_COMMON_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


def _find_usable_bash() -> str | None:
    candidates = []
    seen = set()

    env_candidate = os.environ.get("BASH_PATH")
    if env_candidate:
        candidates.append(env_candidate)

    which_candidate = shutil.which("bash")
    if which_candidate:
        candidates.append(which_candidate)

    candidates.extend(_COMMON_BASH_CANDIDATES)

    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return candidate
    return None


@pytest.fixture
def bash_path() -> str:
    candidate = _find_usable_bash()
    if candidate is None:
        pytest.skip("usable bash executable is not available in this environment")
    return candidate


def _write_executable(path: Path, body: str) -> None:
    path.write_text(
        "#!/usr/bin/env bash\nset -e\n" + textwrap.dedent(body).lstrip(),
        encoding="utf-8",
        newline="\n",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _copy_script(name: str, dest_dir: Path) -> Path:
    source = ROOT / name
    target = dest_dir / name
    target.write_bytes(source.read_bytes())
    # Ensure the copied script is executable on POSIX runners (e.g. CI).
    target.chmod(target.stat().st_mode | stat.S_IEXEC)
    return target


def _bash_script_arg(script_path: Path) -> str:
    return f"./{script_path.name}"


def _bash_script_command(script_path: Path, *args: str) -> list[str]:
    command = " ".join(
        shlex.quote(part) for part in (_bash_script_arg(script_path), *args)
    )
    return ["-c", f'PATH="./test-bin:$PATH" exec {command}']


def _write_python_stub(path: Path, *, supports_venv: bool = False) -> None:
    body = """
        printf '%s\n' "$*" >> python.log
        if [ "${1:-}" = "-" ] && [ $# -ge 3 ]; then
            awk -F= -v key="$3" '$1 == key { sub(/\r$/, "", $2); print $2; exit }' "$2"
            exit 0
        fi
        """
    if supports_venv:
        body += """
        if [ "${1:-}" = "-c" ]; then
            exit 0
        fi
        if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
            mkdir -p "$3/bin"
            printf '# test activate\\n' > "$3/bin/activate"
            exit 0
        fi
        if [ "${1:-}" = "--version" ]; then
            echo "Python 3.11.9"
            exit 0
        fi
        echo "Python 3.11.9"
        exit 0
        """
    else:
        body += """
        exit 0
        """
    _write_executable(path, body)


def _read_log(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def test_start_server_uses_dotenv_remote_mode_to_skip_local_ollama(
    tmp_path, bash_path
):
    script_path = _copy_script("start_server.sh", tmp_path)
    (tmp_path / ".env").write_text("OLLAMA_MODE=remote\n", encoding="utf-8")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "activate").write_text(
        "# test activate\n",
        encoding="utf-8",
    )

    bin_dir = tmp_path / "test-bin"
    bin_dir.mkdir()
    _write_python_stub(bin_dir / "python")
    _write_executable(
        bin_dir / "pgrep",
        """
        printf '%s\n' "$*" >> pgrep.log
        exit 1
        """,
    )
    _write_executable(
        bin_dir / "ollama",
        """
        printf '%s\n' "$*" >> ollama.log
        exit 0
        """,
    )

    env = os.environ.copy()
    env.pop("OLLAMA_MODE", None)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")

    result = subprocess.run(
        [bash_path, *_bash_script_command(script_path, "--proxy")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipping local Ollama startup" in result.stdout
    assert "start.py --headless --proxy" in _read_log(tmp_path / "python.log")
    assert _read_log(tmp_path / "pgrep.log") == ""
    assert _read_log(tmp_path / "ollama.log") == ""


# setup_runpod.sh is private GPU-host provisioning tooling and is deliberately
# not published (README, "Known public-release limitations"). The script is not
# recreated here: a stub wrapper would make this test pass while asserting
# nothing about real behaviour. The equivalent test above, for the PUBLISHED
# start_server.sh, remains in the blocking scope.
@pytest.mark.requires_private_assets
def test_setup_runpod_uses_dotenv_remote_mode_to_skip_local_ollama(
    tmp_path, bash_path
):
    script_path = _copy_script("setup_runpod.sh", tmp_path)
    (tmp_path / ".env").write_text(
        "OLLAMA_MODE=remote\nOLLAMA_MODEL=phi4-mini\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.runtime.txt").write_text("", encoding="utf-8")

    bin_dir = tmp_path / "test-bin"
    bin_dir.mkdir()

    for name in ("python3.11", "python3", "python"):
        _write_python_stub(bin_dir / name, supports_venv=True)

    _write_executable(
        bin_dir / "id",
        """
        if [ "${1:-}" = "-u" ]; then
            echo 1000
            exit 0
        fi
        exit 0
        """,
    )
    _write_executable(
        bin_dir / "sudo",
        """
        "$@"
        """,
    )
    _write_executable(
        bin_dir / "apt-get",
        """
        printf '%s\n' "$*" >> apt-get.log
        exit 0
        """,
    )
    _write_executable(
        bin_dir / "pip",
        """
        printf '%s\n' "$*" >> pip.log
        exit 0
        """,
    )
    _write_executable(
        bin_dir / "curl",
        """
        printf '%s\n' "$*" >> curl.log
        exit 0
        """,
    )
    _write_executable(
        bin_dir / "wget",
        """
        printf '%s\n' "$*" >> wget.log
        exit 0
        """,
    )
    _write_executable(
        bin_dir / "pgrep",
        """
        printf '%s\n' "$*" >> pgrep.log
        exit 1
        """,
    )
    _write_executable(
        bin_dir / "ollama",
        """
        printf '%s\n' "$*" >> ollama.log
        exit 0
        """,
    )

    env = os.environ.copy()
    env.pop("OLLAMA_MODE", None)
    env.pop("OLLAMA_MODEL", None)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")

    result = subprocess.run(
        [bash_path, *_bash_script_command(script_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipping local Ollama install and model pull" in result.stdout
    assert "update -qq" in _read_log(tmp_path / "apt-get.log")
    assert "install --upgrade pip -q" in _read_log(tmp_path / "pip.log")
    assert "install -r requirements.runtime.txt -q" in _read_log(tmp_path / "pip.log")
    assert _read_log(tmp_path / "curl.log") == ""
    assert _read_log(tmp_path / "pgrep.log") == ""
    assert _read_log(tmp_path / "ollama.log") == ""
