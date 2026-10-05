"""
Single-command launcher for the local Sign2Text stack.

Starts:
  - ASL backend on port 8000
  - Voice backend on port 8001
  - Static frontend on port 5500

Usage:
  python start.py              # local-only (default)
  python start.py --tunnel     # local + Cloudflare Quick Tunnel for remote testing
  python start.py --proxy      # start reverse proxy on 0.0.0.0:8080 for public single-port deployments
  python start.py --headless   # non-interactive (no prompts, no browser) for server/cloud
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.hardware import log_hardware_report
from backend.runtime_config import (
    DEFAULT_ASL_CHECKPOINT_PATH,
    load_runtime_config,
    read_dotenv as shared_read_dotenv,
    write_frontend_runtime_config_js,
)
from backend.secret_config import (
    find_first_existing_path,
    google_credentials_candidates,
    load_project_dotenv,
    normalize_path_env,
)


ROOT = Path(__file__).resolve().parent
if os.name == "nt":
    VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
else:
    VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
RUNTIME_CONFIG_JS = ROOT / "frontend" / "web" / "runtime-config.js"
SHUTDOWN_WAIT_SEC = 5.0
TUNNEL_PROXY_PORT = 8080
TUNNEL_URL_RE = re.compile(r"(https://[a-z0-9-]+\.trycloudflare\.com)")


class Service:
    def __init__(
        self,
        name: str,
        port: int,
        command: List[str],
        connect_host: str = "127.0.0.1",
    ):
        self.name = name
        self.port = port
        self.command = command
        self.connect_host = connect_host
        self.process: Optional[subprocess.Popen[str]] = None
        self.thread: Optional[threading.Thread] = None
        self.on_line: List[Callable[[str], None]] = []


SERVICES = [
    Service("ASL", 8000, []),
    Service("VOICE", 8001, []),
    Service("LETTER", 8002, []),
    Service("ADMIN", 8003, []),
    Service("FRONTEND", 5500, []),
]


PRINT_LOCK = threading.Lock()
ANSI_RESET = "\033[0m"
COLORS = {
    "ASL": "\033[96m",
    "VOICE": "\033[93m",
    "LETTER": "\033[36m",
    "ADMIN": "\033[33m",
    "FRONTEND": "\033[92m",
    "SYSTEM": "\033[95m",
    "ERROR": "\033[91m",
    "PROXY": "\033[94m",
    "TUNNEL": "\033[35m",
}


def enable_ansi_if_possible() -> None:
    if os.name == "nt":
        os.system("")


def supports_ansi() -> bool:
    return sys.stdout.isatty()


def format_prefix(tag: str) -> str:
    prefix = f"[{tag}]"
    if supports_ansi():
        color = COLORS.get(tag, "")
        if color:
            return f"{color}{prefix}{ANSI_RESET}"
    return prefix


def _sanitize_console_text(message: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None)
    if not encoding:
        return message
    try:
        message.encode(encoding)
        return message
    except UnicodeEncodeError:
        return message.encode(encoding, errors="replace").decode(
            encoding,
            errors="replace",
        )


def log(message: str, tag: str = "SYSTEM") -> None:
    with PRINT_LOCK:
        safe_message = _sanitize_console_text(str(message))
        print(f"{format_prefix(tag)} {safe_message}", flush=True)


def read_dotenv(path: Path) -> Dict[str, str]:
    return shared_read_dotenv(path)


def resolve_path_var(env: Dict[str, str], key: str) -> None:
    value = env.get(key, "").strip()
    if not value:
        return
    # Accept Windows-style backslashes from .env on POSIX runners (CI).
    path = Path(value.replace("\\", "/"))
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    env[key] = str(path)


def build_child_env() -> Dict[str, str]:
    dotenv_env = load_project_dotenv(ROOT)
    env = dict(os.environ)
    env.update(dotenv_env)

    for key in (
        "GOOGLE_STT_CREDENTIALS",
        "GOOGLE_TTS_CREDENTIALS",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "STT_KEY_PATH",
        "TTS_KEY_PATH",
    ):
        normalize_path_env(env, key, ROOT)

    discovery_env = dict(env)
    if not env.get("GOOGLE_STT_CREDENTIALS"):
        discovered_stt = find_first_existing_path(
            google_credentials_candidates(
                "stt",
                project_root=ROOT,
                environ=discovery_env,
            )
        )
        if discovered_stt is not None:
            env["GOOGLE_STT_CREDENTIALS"] = str(discovered_stt)

    if not env.get("GOOGLE_TTS_CREDENTIALS"):
        discovered_tts = find_first_existing_path(
            google_credentials_candidates(
                "tts",
                project_root=ROOT,
                environ=discovery_env,
            )
        )
        if discovered_tts is not None:
            env["GOOGLE_TTS_CREDENTIALS"] = str(discovered_tts)

    if not env.get("GOOGLE_APPLICATION_CREDENTIALS"):
        shared_creds = env.get("GOOGLE_TTS_CREDENTIALS") or env.get("GOOGLE_STT_CREDENTIALS")
        if shared_creds:
            env["GOOGLE_APPLICATION_CREDENTIALS"] = shared_creds

    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def validate_required_files(env: Dict[str, str]) -> None:
    # Purpose-specific credential vars: if explicitly set, the path must exist —
    # a missing file here is almost always a user typo worth surfacing.
    primary_keys = (
        "GOOGLE_STT_CREDENTIALS",
        "GOOGLE_TTS_CREDENTIALS",
        "STT_KEY_PATH",
        "TTS_KEY_PATH",
    )
    valid_primary: Dict[str, str] = {}
    for key in primary_keys:
        value = env.get(key, "").strip()
        if not value:
            continue
        if not Path(value).exists():
            log(f"{key} path does not exist: {value}", "ERROR")
            raise SystemExit(1)
        valid_primary[key] = value

    # GOOGLE_APPLICATION_CREDENTIALS is a generic fallback used by Google client
    # libraries when no purpose-specific key is set. Don't kill startup if it
    # points to a stale path — substitute a valid primary credential when
    # possible, otherwise drop it so libraries fall back to ADC discovery.
    fallback_key = "GOOGLE_APPLICATION_CREDENTIALS"
    fallback_value = env.get(fallback_key, "").strip()
    if fallback_value and not Path(fallback_value).exists():
        replacement = (
            valid_primary.get("GOOGLE_TTS_CREDENTIALS")
            or valid_primary.get("GOOGLE_STT_CREDENTIALS")
            or valid_primary.get("TTS_KEY_PATH")
            or valid_primary.get("STT_KEY_PATH")
        )
        if replacement:
            log(
                f"{fallback_key} points to a missing file ({fallback_value}); "
                f"using {replacement} instead.",
                "SYSTEM",
            )
            env[fallback_key] = replacement
            fallback_value = replacement
        else:
            log(
                f"{fallback_key} points to a missing file ({fallback_value}) "
                "and no other Google service-account file was found. "
                "Unsetting it so the runtime can fall back to ADC or "
                "backend-side credential discovery.",
                "SYSTEM",
            )
            env.pop(fallback_key, None)
            fallback_value = ""

    if not valid_primary and not fallback_value:
        log(
            "No Google service-account file was configured for the launcher. "
            "Startup will rely on ADC or backend-side credential discovery.",
            "SYSTEM",
        )


def validate_asl_checkpoint(config: Any) -> None:
    checkpoint_path = Path(str(getattr(config, "asl_checkpoint_path", "")).strip())
    if checkpoint_path.is_file():
        return

    log(
        "ASL checkpoint file not found. "
        f"Resolved ASL_CHECKPOINT_PATH to: {checkpoint_path}. "
        f"Place the checkpoint at {DEFAULT_ASL_CHECKPOINT_PATH} or set "
        "ASL_CHECKPOINT_PATH to the correct file before starting.",
        "ERROR",
    )
    raise SystemExit(1)


def choose_python() -> str:
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    return sys.executable


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def connect_host_for_bind_host(bind_host: str) -> str:
    host = (bind_host or "").strip()
    if not host or host in ("0.0.0.0", "::", "[::]", "localhost"):
        return "127.0.0.1"
    return host


def get_port_pids(port: int) -> List[int]:
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return []

    pids = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        state = parts[3].upper()
        pid_text = parts[4]
        if not local_addr.endswith(f":{port}"):
            continue
        if state != "LISTENING":
            continue
        try:
            pids.add(int(pid_text))
        except ValueError:
            continue
    return sorted(pids)


def get_pid_name(pid: int) -> str:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return "unknown"

    line = result.stdout.strip()
    if not line or line.startswith("INFO:"):
        return "unknown"

    if line.startswith('"') and '","' in line:
        return line.split('","', 1)[0].strip('"')
    return line


def kill_pid_tree(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def handle_port_conflicts() -> None:
    for service in SERVICES:
        if not is_port_in_use(service.port, service.connect_host):
            continue

        pids = get_port_pids(service.port)
        log(
            f"Port {service.port} is already in use for {service.name}.",
            "ERROR",
        )

        if pids:
            for pid in pids:
                log(
                    f"Port {service.port} -> PID {pid} ({get_pid_name(pid)})",
                    "ERROR",
                )
        else:
            log(
                f"Could not resolve the PID for port {service.port}.",
                "ERROR",
            )

        while True:
            choice = input(
                f"Kill the existing process on port {service.port}? [k]ill/[a]bort: "
            ).strip().lower()
            if choice in ("a", "abort", ""):
                raise SystemExit(1)
            if choice in ("k", "kill"):
                if not pids:
                    log(
                        f"Cannot kill port {service.port} automatically without a PID.",
                        "ERROR",
                    )
                    raise SystemExit(1)
                for pid in pids:
                    if kill_pid_tree(pid):
                        log(f"Killed PID {pid} on port {service.port}.")
                    else:
                        log(f"Failed to kill PID {pid}.", "ERROR")
                        raise SystemExit(1)
                time.sleep(1.0)
                if is_port_in_use(service.port, service.connect_host):
                    log(f"Port {service.port} is still occupied after kill.", "ERROR")
                    raise SystemExit(1)
                break
            log("Please choose 'k' to kill or 'a' to abort.", "SYSTEM")


def stream_output(service: Service) -> None:
    assert service.process is not None
    assert service.process.stdout is not None
    for line in service.process.stdout:
        text = line.rstrip("\r\n")
        if text:
            log(text, service.name)
            for callback in service.on_line:
                callback(text)


def launch_service(service: Service, env: Dict[str, str]) -> None:
    kwargs: Dict[str, Any] = dict(
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    if os.name == "nt":
        kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True
    service.process = subprocess.Popen(service.command, **kwargs)
    service.thread = threading.Thread(
        target=stream_output,
        args=(service,),
        daemon=True,
    )
    service.thread.start()


def wait_for_readiness(timeout_sec: float) -> None:
    deadline = time.time() + timeout_sec
    pending = {service.name: service.port for service in SERVICES}

    while pending and time.time() < deadline:
        for service in SERVICES:
            proc = service.process
            if proc is not None and proc.poll() is not None:
                raise RuntimeError(
                    f"{service.name} exited early with code {proc.returncode}."
                )
        for service in SERVICES:
            if service.name not in pending:
                continue
            if is_port_in_use(service.port, service.connect_host):
                pending.pop(service.name, None)
        time.sleep(0.2)

    if pending:
        waiting = ", ".join(f"{name}:{port}" for name, port in pending.items())
        raise RuntimeError(f"Timed out waiting for services: {waiting}")


def print_banner(
    config,
    tunnel_url: Optional[str] = None,
    proxy_mode: bool = False,
) -> None:
    line = "=" * 48
    with PRINT_LOCK:
        print(line, flush=True)
        print("  Sign2Text is running!", flush=True)
        print(f"  Frontend:  {config.frontend_launch_url}", flush=True)
        print(f"  ASL API:   http://127.0.0.1:{config.asl_port}", flush=True)
        print(f"  Voice API: http://127.0.0.1:{config.voice_port}", flush=True)
        print(f"  Letter API: http://127.0.0.1:{config.letter_port}", flush=True)
        print(f"  Admin API: http://127.0.0.1:{config.admin_port}", flush=True)
        if tunnel_url:
            print("  " + "-" * 44, flush=True)
            print(f"  Tunnel:    {tunnel_url}", flush=True)
            print("  Share this URL for remote testing!", flush=True)
        elif proxy_mode:
            print("  " + "-" * 44, flush=True)
            if getattr(config, "public_host_explicit", False):
                public_url = f"{config.public_scheme}://{config.public_host}"
                print(f"  Public URL: {public_url}", flush=True)
                print(f"  (Proxy listens on 0.0.0.0:{TUNNEL_PROXY_PORT} behind your ingress)", flush=True)
            else:
                print(f"  Proxy:     http://0.0.0.0:{TUNNEL_PROXY_PORT}", flush=True)
                print("  All services consolidated on this port.", flush=True)
        print(line, flush=True)
        print("Press Ctrl+C to stop all services.", flush=True)


def open_browser_later(frontend_url: str) -> None:
    time.sleep(1.0)
    try:
        webbrowser.open(frontend_url)
    except Exception:
        log("Failed to auto-open the browser.", "ERROR")


def stop_service(service: Service) -> None:
    proc = service.process
    if proc is None or proc.poll() is not None:
        return

    if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            return
        except Exception:
            pass

    try:
        proc.terminate()
    except Exception:
        pass


def force_kill_service(service: Service) -> None:
    proc = service.process
    if proc is None or proc.poll() is not None:
        return

    try:
        if os.name == "nt":
            if not kill_pid_tree(proc.pid):
                proc.kill()
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def shutdown(services: List[Service]) -> None:
    log("Stopping all services...")
    for service in services:
        stop_service(service)

    deadline = time.time() + SHUTDOWN_WAIT_SEC
    while time.time() < deadline:
        if all(
            service.process is None or service.process.poll() is not None
            for service in services
        ):
            break
        time.sleep(0.2)

    for service in services:
        force_kill_service(service)

    for service in services:
        if service.thread is not None:
            service.thread.join(timeout=1.0)

    log("All services stopped.")


def configure_commands(config) -> None:
    python = choose_python()
    SERVICES[0].port = config.asl_port
    SERVICES[0].connect_host = connect_host_for_bind_host(config.asl_bind_host)
    SERVICES[0].command = [
        python,
        "-m", "backend.api_server",
        "--host",
        config.asl_bind_host,
        "--port",
        str(config.asl_port),
    ]
    SERVICES[1].port = config.voice_port
    SERVICES[1].connect_host = connect_host_for_bind_host(config.voice_bind_host)
    SERVICES[1].command = [
        python,
        "-m",
        "uvicorn",
        "backend.stt_backend:app",
        "--host",
        config.voice_bind_host,
        "--port",
        str(config.voice_port),
    ]
    SERVICES[2].port = config.letter_port
    SERVICES[2].connect_host = connect_host_for_bind_host(config.letter_bind_host)
    SERVICES[2].command = [
        python,
        "-m", "backend.letter_server",
        "--host",
        config.letter_bind_host,
        "--port",
        str(config.letter_port),
    ]
    admin_bind_host = str(os.environ.get("ADMIN_BIND_HOST", config.asl_bind_host)).strip() or config.asl_bind_host
    admin_port = config.admin_port
    SERVICES[3].port = admin_port
    SERVICES[3].connect_host = connect_host_for_bind_host(admin_bind_host)
    SERVICES[3].command = [
        python,
        "-m", "backend.admin_server",
        "--host",
        admin_bind_host,
        "--port",
        str(admin_port),
    ]
    SERVICES[4].port = config.frontend_port
    SERVICES[4].connect_host = connect_host_for_bind_host(config.frontend_bind_host)
    SERVICES[4].command = [
        python,
        "-m",
        "backend.frontend_server",
        str(config.frontend_port),
        "--bind",
        config.frontend_bind_host,
    ]
    landing_index = ROOT / "landing" / "dist" / "index.html"
    if landing_index.exists():
        log("Landing page detected - serving landing/dist at /", "FRONTEND")
    else:
        log("No landing/dist found - serving index.html at /", "FRONTEND")


def monitor_loop() -> None:
    while True:
        for service in SERVICES:
            proc = service.process
            if proc is not None and proc.poll() is not None:
                raise RuntimeError(
                    f"{service.name} exited with code {proc.returncode}."
                )
        time.sleep(0.5)


def main() -> int:
    enable_ansi_if_possible()
    tunnel_mode = "--tunnel" in sys.argv
    proxy_mode = "--proxy" in sys.argv
    headless_mode = "--headless" in sys.argv

    env = build_child_env()

    # In tunnel or proxy mode, inject path-based URL overrides so that
    # runtime-config.js tells the frontend to use the proxy paths
    # instead of direct port-based URLs.
    if tunnel_mode or proxy_mode:
        env["ASL_API_BASE_URL"] = "/asl-api"
        env["VOICE_API_BASE_URL"] = "/voice-api"
        env["VOICE_WS_URL"] = "/voice-api/ws/stt"
        env["LETTER_API_BASE_URL"] = "/letter-api"
        env["ADMIN_API_BASE_URL"] = "/admin-api"

    config = load_runtime_config(ROOT, env)
    validate_asl_checkpoint(config)
    log_hardware_report(config.hardware_report)
    env.setdefault("ASL_RUNTIME_PROFILE", config.hardware_profile)
    env.setdefault("ASL_CHECKPOINT_PATH", config.asl_checkpoint_path)
    env.setdefault("OLLAMA_URL", config.ollama_url)
    env.setdefault("OLLAMA_MODEL", config.ollama_model)
    env.setdefault("OLLAMA_TIMEOUT_SEC", str(config.ollama_timeout_sec))
    if config.ollama_auth_token:
        env.setdefault("OLLAMA_AUTH_TOKEN", config.ollama_auth_token)
    env.setdefault("OLLAMA_MODE", config.ollama_mode)
    write_frontend_runtime_config_js(RUNTIME_CONFIG_JS, config)
    configure_commands(config)
    validate_required_files(env)

    # In headless mode, skip interactive port conflict prompts —
    # just fail if a port is already occupied.
    if headless_mode:
        for service in SERVICES:
            if is_port_in_use(service.port, service.connect_host):
                log(
                    f"Port {service.port} ({service.name}) is already in use. "
                    "Cannot start in headless mode.",
                    "ERROR",
                )
                return 1
    else:
        handle_port_conflicts()

    log("Starting Sign2Text services...")
    launched: List[Service] = []

    try:
        for service in SERVICES:
            launch_service(service, env)
            launched.append(service)

        wait_for_readiness(config.service_timeout_sec)

        tunnel_url: Optional[str] = None

        if proxy_mode:
            _start_proxy(launched, env)
        elif tunnel_mode:
            tunnel_url = _start_tunnel(launched, env, config)

        print_banner(config, tunnel_url=tunnel_url, proxy_mode=proxy_mode)

        # Skip browser auto-open in headless mode (e.g. RunPod server)
        if not headless_mode:
            threading.Thread(
                target=open_browser_later,
                args=(config.frontend_launch_url,),
                daemon=True,
            ).start()

        monitor_loop()
        return 0

    except KeyboardInterrupt:
        log("Ctrl+C received. Shutting down...")
        shutdown(list(reversed(launched)))
        return 0
    except Exception as exc:
        log(str(exc), "ERROR")
        shutdown(list(reversed(launched)))
        return 1


def _start_proxy(
    launched: List[Service],
    env: Dict[str, str],
) -> None:
    """Launch the reverse proxy on 0.0.0.0 (no Cloudflare) for cloud deployments."""
    python = choose_python()
    proxy_service = Service("PROXY", TUNNEL_PROXY_PORT, [
        python,
        "-m", "uvicorn",
        "tunnel_proxy:app",
        "--host", "0.0.0.0",
        "--port", str(TUNNEL_PROXY_PORT),
    ])
    launch_service(proxy_service, env)
    launched.append(proxy_service)

    deadline = time.time() + 15.0
    while not is_port_in_use(TUNNEL_PROXY_PORT) and time.time() < deadline:
        time.sleep(0.3)
    if not is_port_in_use(TUNNEL_PROXY_PORT):
        log("Proxy failed to start on port " + str(TUNNEL_PROXY_PORT), "ERROR")
        raise SystemExit(1)
    log(f"Reverse proxy ready on 0.0.0.0:{TUNNEL_PROXY_PORT}.", "PROXY")


def _start_tunnel(
    launched: List[Service],
    env: Dict[str, str],
    config,
) -> Optional[str]:
    """Launch the reverse proxy and cloudflared, return the tunnel URL."""
    python = choose_python()

    # ── 1. Check that cloudflared is available ──────────────────────────
    cloudflared_path = shutil.which("cloudflared")
    if not cloudflared_path:
        log(
            "cloudflared not found on PATH. "
            "Install it from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/ "
            "or run: winget install cloudflare.cloudflared",
            "ERROR",
        )
        raise SystemExit(1)

    # ── 2. Start the reverse proxy ──────────────────────────────────────
    proxy_service = Service("PROXY", TUNNEL_PROXY_PORT, [
        python,
        "-m", "uvicorn",
        "tunnel_proxy:app",
        "--host", "127.0.0.1",
        "--port", str(TUNNEL_PROXY_PORT),
    ])
    launch_service(proxy_service, env)
    launched.append(proxy_service)

    deadline = time.time() + 15.0
    while not is_port_in_use(TUNNEL_PROXY_PORT) and time.time() < deadline:
        time.sleep(0.3)
    if not is_port_in_use(TUNNEL_PROXY_PORT):
        log("Tunnel proxy failed to start on port " + str(TUNNEL_PROXY_PORT), "ERROR")
        raise SystemExit(1)
    log(f"Tunnel proxy ready on port {TUNNEL_PROXY_PORT}.", "PROXY")

    # ── 3. Launch cloudflared Quick Tunnel ──────────────────────────────
    tunnel_url_event = threading.Event()
    tunnel_url_holder: List[Optional[str]] = [None]

    def _capture_url(line: str) -> None:
        match = TUNNEL_URL_RE.search(line)
        if match and tunnel_url_holder[0] is None:
            tunnel_url_holder[0] = match.group(1)
            tunnel_url_event.set()

    tunnel_service = Service("TUNNEL", 0, [
        cloudflared_path,
        "tunnel",
        "--url", f"http://localhost:{TUNNEL_PROXY_PORT}",
    ])
    tunnel_service.on_line.append(_capture_url)
    launch_service(tunnel_service, env)
    launched.append(tunnel_service)

    log("Waiting for Cloudflare tunnel URL...", "TUNNEL")
    if not tunnel_url_event.wait(timeout=30):
        log(
            "Timed out waiting for cloudflared to provide a tunnel URL. "
            "Check that cloudflared is working correctly.",
            "ERROR",
        )
        raise SystemExit(1)

    return tunnel_url_holder[0]


if __name__ == "__main__":
    raise SystemExit(main())
