"""Native Windows shell for the Q-xApp simulator dashboard."""

from __future__ import annotations

import argparse
import ctypes
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import webview


DEFAULT_URL = "http://127.0.0.1:8000/?capture"
DEFAULT_TITLE = "Q-xApp O-RAN Network Simulator"
APP_USER_MODEL_ID = "KoreaUniversity.LICS.QxAppSimulator"
DEFAULT_WSL_DISTRO = os.environ.get("QXAPP_WSL_DISTRO", "Ubuntu")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="Q-xApp dashboard URL")
    parser.add_argument("--title", default=DEFAULT_TITLE, help="Native window title")
    parser.add_argument("--width", type=int, default=1700)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument(
        "--wsl-distro",
        default=DEFAULT_WSL_DISTRO,
        help="WSL distribution hosting the Q-xApp GUI",
    )
    parser.add_argument(
        "--host-data",
        default=os.environ.get("QXAPP_HOST_DATA", ""),
        help="WSL path containing ns-3/Q-xApp runtime data",
    )
    parser.add_argument(
        "--no-start-backend",
        action="store_true",
        help="Do not start or keep alive the local WSL/Docker GUI backend",
    )
    return parser.parse_args()


def windows_path_to_wsl(path: Path) -> str:
    """Convert a local Windows path without depending on an active WSL session."""
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if not drive:
        raise RuntimeError(f"Cannot convert path to WSL: {resolved}")
    suffix = resolved.as_posix().split(":", 1)[1]
    return f"/mnt/{drive}{suffix}"


def start_backend_and_keepalive(
    distro: str, host_data: str = "",
) -> tuple[subprocess.Popen[bytes], object]:
    """Start the GUI stack and keep WSL alive while the native window is open."""
    if not host_data:
        raise RuntimeError(
            "QXAPP_HOST_DATA is required. Pass --host-data with the WSL path "
            "to the ns-3 run directory."
        )
    gui_dir = windows_path_to_wsl(Path(__file__).parents[1])
    backend_log_path = (
        Path(os.environ.get("LOCALAPPDATA", Path.home()))
        / "QxAppDesktop"
        / "backend.log"
    )
    backend_log_path.parent.mkdir(parents=True, exist_ok=True)
    backend_log = backend_log_path.open("ab", buffering=0)
    compose = (
        f"QXAPP_HOST_DATA={shlex.quote(host_data)} "
        "docker compose up -d"
    )
    command = " ".join(
        (
            "/usr/sbin/shutdown -c >/dev/null 2>&1 || true;",
            "systemctl start docker;",
            f"cd {shlex.quote(gui_dir)};",
            f"{compose};",
            "exec bash -c 'while :; do sleep 3600; done'",
        )
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [
            "wsl.exe",
            "-d",
            distro,
            "-u",
            "root",
            "--exec",
            "bash",
            "-lc",
            command,
        ],
        stdin=subprocess.DEVNULL,
        stdout=backend_log,
        stderr=subprocess.STDOUT,
        creationflags=creation_flags,
    )
    return process, backend_log


def stop_backend_keepalive(
    process: subprocess.Popen[bytes] | None, backend_log: object | None
) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
    if backend_log is not None:
        backend_log.close()


def wait_for_dashboard(
    url: str,
    backend_process: subprocess.Popen[bytes] | None = None,
    attempts: int = 120,
    delay: float = 0.5,
) -> None:
    """Wait through WSL/Docker startup before reporting a real backend failure."""
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status < 500:
                    return
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            if backend_process is not None and backend_process.poll() is not None:
                break
            time.sleep(delay)
    raise RuntimeError(
        f"Q-xApp GUI is not available at {url}\n\n"
        "The local WSL/Docker backend could not be started. "
        "See %LOCALAPPDATA%\\QxAppDesktop\\backend.log."
    ) from last_error


def show_error(message: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(
            None, message, "Q-xApp Simulator", 0x00000010
        )
    else:
        print(message, file=sys.stderr)


def find_window(title: str, timeout: float = 10.0) -> int | None:
    user32 = ctypes.windll.user32
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
    )

    def enum_callback(hwnd: int, _lparam: int) -> bool:
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value == title:
                found.append(hwnd)
                return False
        return True

    callback = callback_type(enum_callback)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found.clear()
        user32.EnumWindows(callback, 0)
        if found:
            return found[0]
        time.sleep(0.1)
    return None


def colorref(hex_color: str) -> int:
    red, green, blue = bytes.fromhex(hex_color.lstrip("#"))
    return red | (green << 8) | (blue << 16)


def apply_native_chrome(_window: webview.Window, title: str) -> None:
    """Apply dark Windows chrome and the Q-xApp application icon."""
    if sys.platform != "win32":
        return

    dwmapi = ctypes.windll.dwmapi
    user32 = ctypes.windll.user32
    dwmapi.DwmSetWindowAttribute.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

    def set_dwm_attribute(hwnd: int, attribute: int, value: int) -> None:
        data = ctypes.c_int(value)
        dwmapi.DwmSetWindowAttribute(
            hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)
        )

    # WebView2 finishes applying its host window style shortly after creation.
    # Reapply the dark title-bar flag so it is not overwritten during startup.
    for _ in range(24):
        hwnd = find_window(title, timeout=1.0)
        if hwnd:
            set_dwm_attribute(hwnd, 20, 1)
            set_dwm_attribute(hwnd, 35, colorref("#080c10"))
            set_dwm_attribute(hwnd, 36, colorref("#edf2f7"))
            set_dwm_attribute(hwnd, 34, colorref("#293342"))
        time.sleep(0.25)

    hwnd = find_window(title, timeout=1.0)
    icon_path = Path(__file__).with_name("qxapp.ico")
    if hwnd and icon_path.exists():
        image_icon = 1
        load_from_file = 0x0010
        default_size = 0x0040
        icon = user32.LoadImageW(
            None,
            str(icon_path),
            image_icon,
            0,
            0,
            load_from_file | default_size,
        )
        if icon:
            wm_seticon = 0x0080
            user32.SendMessageW(hwnd, wm_seticon, 0, icon)
            user32.SendMessageW(hwnd, wm_seticon, 1, icon)


def main() -> int:
    args = parse_args()
    backend_process: subprocess.Popen[bytes] | None = None
    backend_log: object | None = None
    try:
        if sys.platform == "win32" and not args.no_start_backend:
            backend_process, backend_log = start_backend_and_keepalive(
                args.wsl_distro, args.host_data
            )
        wait_for_dashboard(args.url, backend_process)
    except RuntimeError as error:
        stop_backend_keepalive(backend_process, backend_log)
        show_error(str(error))
        return 1

    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            APP_USER_MODEL_ID
        )

    window = webview.create_window(
        args.title,
        args.url,
        width=args.width,
        height=args.height,
        min_size=(1280, 720),
        resizable=True,
        background_color="#06080c",
        text_select=False,
    )
    try:
        webview.start(
            apply_native_chrome,
            (window, args.title),
            gui="edgechromium",
            debug=False,
            private_mode=True,
        )
    finally:
        stop_backend_keepalive(backend_process, backend_log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
