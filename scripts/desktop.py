"""Native Windows Desktop System Tray Launcher for Adjutant Appliance.

Orchestrates local services (API server, Autonomous Runner daemon), manages Windows
autostart, tracks license lease status, and provides system tray access.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("desktop_appliance")

# Windows constants for winreg and Win32 tray API
IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    import ctypes
    import winreg
    from ctypes import wintypes

    REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
    REG_NAME = "AdjutantAppliance"

    WM_USER = 0x0400
    WM_TRAYICON = WM_USER + 20
    WM_COMMAND = 0x0111
    WM_DESTROY = 0x0002
    WM_LBUTTONUP = 0x0202
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205

    NIM_ADD = 0x00000000
    NIM_MODIFY = 0x00000001
    NIM_DELETE = 0x00000002
    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004

    MF_STRING = 0x00000000
    MF_SEPARATOR = 0x00000800
    MF_CHECKED = 0x00000008
    MF_UNCHECKED = 0x00000000
    MF_GRAYED = 0x00000001
    MF_DEFAULT = 0x00001000
    TPM_RIGHTBUTTON = 0x0002
    TPM_LEFTALIGN = 0x0000

    IDI_APPLICATION = 32512
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x00000010
    LR_DEFAULTSIZE = 0x00000040

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]

    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_long,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HICON),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]


def is_autostart_enabled() -> bool:
    """Check if Adjutant is registered in Windows CurrentVersion\\Run."""
    if not IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, REG_NAME)
            return bool(val)
    except OSError:
        return False


def set_autostart(enable: bool) -> None:
    """Enable or disable Windows autostart on boot."""
    if not IS_WINDOWS:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                if getattr(sys, "frozen", False):
                    cmd = f'"{sys.executable}"'
                else:
                    cmd = f'"{sys.executable}" "{Path(__file__).resolve()}"'
                winreg.SetValueEx(key, REG_NAME, 0, winreg.REG_SZ, cmd)
                logger.info("Windows autostart enabled: %s", cmd)
            else:
                try:
                    winreg.DeleteValue(key, REG_NAME)
                    logger.info("Windows autostart disabled.")
                except FileNotFoundError:
                    pass
    except OSError as err:
        logger.warning("Failed to update Windows autostart registry key: %s", err)


class ApplianceManager:
    """Manages background service lifecycle, health probes, and status telemetry."""

    def __init__(
        self,
        web_port: int = 3000,
        api_port: int = 8000,
        db_port: int = 55439,
        state_dir: Path | None = None,
    ) -> None:
        self.web_port = web_port
        self.api_port = api_port
        self.db_port = db_port
        self.state_dir = state_dir or (ROOT / ".local" / "runner")
        self.orchestrator_proc: subprocess.Popen[Any] | None = None
        self.running = False
        self.license_status: dict[str, Any] = {}
        self.runner_status = "Initializing"

    def is_api_alive(self) -> bool:
        """Check if the backend API service is already running and responding."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for path in ("/healthz", "/readyz"):
            try:
                with opener.open(f"http://127.0.0.1:{self.api_port}{path}", timeout=1.0) as res:
                    if res.status == 200:
                        return True
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
        return False

    def start_services(self) -> bool:
        """Connect to existing services or launch the database and API orchestrator."""
        self.state_dir.mkdir(parents=True, exist_ok=True)

        if self.is_api_alive():
            logger.info(
                "Adjutant backend API is already running on port %s. Connecting...",
                self.api_port,
            )
            self.running = True
            self.runner_status = "Active"
            self.poll_status()
            return True

        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        up_script = ROOT / "scripts" / "up.py"

        logger.info(
            "Starting Adjutant appliance (API port %s, db port %s, web port %s)...",
            self.api_port,
            self.db_port,
            self.web_port,
        )
        self.orchestrator_proc = subprocess.Popen(
            [
                sys.executable,
                str(up_script),
                "--port",
                str(self.api_port),
                "--db-port",
                str(self.db_port),
                "--state-directory",
                str(self.state_dir),
            ],
            cwd=ROOT,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0,
        )
        self.running = True

        # Wait for readiness on /readyz or /healthz
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.orchestrator_proc.poll() is not None:
                logger.error(
                    "Adjutant orchestrator exited prematurely with code %s",
                    self.orchestrator_proc.returncode,
                )
                return False
            if self.is_api_alive():
                logger.info(
                    "Adjutant appliance is online. Web console at http://127.0.0.1:%s",
                    self.web_port,
                )
                self.runner_status = "Active"
                self.poll_status()
                return True
            time.sleep(0.5)

        logger.error("Timed out waiting for Adjutant service readiness.")
        return False

    def poll_status(self) -> None:
        """Query /api/status to update current license and runner health telemetry."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.api_port}/api/status",
                headers={"X-Adjutant-Client": "console"},
            )
            with opener.open(req, timeout=2.0) as res:
                if res.status == 200:
                    data = json.loads(res.read().decode("utf-8"))
                    self.license_status = data.get("license", {})
                    self.runner_status = "Active"
        except Exception:
            pass

    def stop_services(self) -> None:
        """Gracefully shut down orchestrator and child processes."""
        self.running = False
        if self.orchestrator_proc and self.orchestrator_proc.poll() is None:
            logger.info("Stopping Adjutant appliance orchestrator...")
            self.orchestrator_proc.terminate()
            try:
                self.orchestrator_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.orchestrator_proc.kill()
                self.orchestrator_proc.wait(timeout=5)
            logger.info("Adjutant appliance services stopped.")


class WindowsSystemTray:
    """Windows system tray notification area integration via Win32 ctypes."""

    # Menu command identifiers
    CMD_OPEN = 1001
    CMD_LICENSE = 1002
    CMD_RUNNER = 1003
    CMD_AUTOSTART = 1004
    CMD_LOGS = 1005
    CMD_EXIT = 1006

    def __init__(self, manager: ApplianceManager, no_browser: bool = False) -> None:
        self.manager = manager
        self.no_browser = no_browser
        self.hwnd: int = 0
        self.hicon: int = 0
        self.class_atom: int = 0
        self._wndproc: Any = None
        self.nid: Any = None

    def create_tray_window(self) -> bool:
        if not IS_WINDOWS:
            return False

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hinstance = kernel32.GetModuleHandleW(None)
        class_name = "AdjutantTrayWindowClass"

        # Load branded icon or default system application icon
        icon_path = ROOT / "assets" / "app_icon.ico"
        if icon_path.exists():
            self.hicon = user32.LoadImageW(
                None,
                str(icon_path),
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
        if not self.hicon:
            self.hicon = user32.LoadIconW(0, IDI_APPLICATION)

        self._wndproc = WNDPROC(self._window_proc)
        wndclass = WNDCLASSW()
        wndclass.style = 0
        wndclass.lpfnWndProc = self._wndproc
        wndclass.cbClsExtra = 0
        wndclass.cbWndExtra = 0
        wndclass.hInstance = hinstance
        wndclass.hIcon = self.hicon
        wndclass.hCursor = user32.LoadCursorW(0, 32512)
        wndclass.hbrBackground = 0
        wndclass.lpszMenuName = None
        wndclass.lpszClassName = class_name

        self.class_atom = user32.RegisterClassW(ctypes.byref(wndclass))
        if not self.class_atom:
            # Class might already be registered in this process
            err = kernel32.GetLastError()
            if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                logger.warning("RegisterClassW failed: %s", err)

        self.hwnd = user32.CreateWindowExW(
            0,
            class_name,
            "Adjutant Appliance",
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            hinstance,
            None,
        )
        if not self.hwnd:
            logger.error("CreateWindowExW failed to create tray host window.")
            return False

        user32.UpdateWindow(self.hwnd)

        self.nid = NOTIFYICONDATAW()
        self.nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.nid.hWnd = self.hwnd
        self.nid.uID = 1
        self.nid.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP
        self.nid.uCallbackMessage = WM_TRAYICON
        self.nid.hIcon = self.hicon
        self.nid.szTip = "Adjutant Appliance - Active"

        shell32 = ctypes.windll.shell32
        res = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid))
        return bool(res)

    def _window_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        user32 = ctypes.windll.user32
        if msg == WM_TRAYICON:
            if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._open_console()
                return 0
            if lparam == WM_RBUTTONUP:
                self._show_context_menu()
                return 0
        elif msg == WM_COMMAND:
            cmd = wparam & 0xFFFF
            if cmd == self.CMD_OPEN:
                self._open_console()
            elif cmd == self.CMD_LICENSE:
                self._open_console()
            elif cmd == self.CMD_AUTOSTART:
                current = is_autostart_enabled()
                set_autostart(not current)
            elif cmd == self.CMD_LOGS:
                self._open_logs()
            elif cmd == self.CMD_EXIT:
                user32.PostQuitMessage(0)
            return 0
        elif msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_context_menu(self) -> None:
        user32 = ctypes.windll.user32
        hmenu = user32.CreatePopupMenu()

        # Update telemetry
        self.manager.poll_status()
        lic = self.manager.license_status
        lic_text = "License: Active"
        if lic:
            st = lic.get("status", "unlicensed")
            if st == "active":
                lic_text = f"License: Active ({lic.get('tier_display', 'Pro')})"
            elif st == "grace_period":
                lic_text = f"License: Grace Period ({lic.get('days_remaining', 0)}d left)"
            elif lic.get("dev_mode"):
                lic_text = "License: Dev Mode (Bypassed)"
            else:
                lic_text = "License: Unlicensed / Expired"

        runner_text = f"Runner: {self.manager.runner_status}"

        # Bold default action: Open Console
        user32.AppendMenuW(hmenu, MF_STRING | MF_DEFAULT, self.CMD_OPEN, "Open Adjutant Console")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, self.CMD_LICENSE, lic_text)
        user32.AppendMenuW(hmenu, MF_STRING | MF_GRAYED, self.CMD_RUNNER, runner_text)
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)

        autostart_flag = MF_CHECKED if is_autostart_enabled() else MF_UNCHECKED
        user32.AppendMenuW(
            hmenu,
            MF_STRING | autostart_flag,
            self.CMD_AUTOSTART,
            "Start with Windows",
        )
        user32.AppendMenuW(hmenu, MF_STRING, self.CMD_LOGS, "View Server Logs")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, self.CMD_EXIT, "Exit Adjutant")

        # Determine mouse pointer coordinates
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        user32.TrackPopupMenu(
            hmenu,
            TPM_RIGHTBUTTON | TPM_LEFTALIGN,
            pt.x,
            pt.y,
            0,
            self.hwnd,
            None,
        )
        user32.DestroyMenu(hmenu)

    def _open_console(self) -> None:
        url = f"http://127.0.0.1:{self.manager.web_port}"
        webbrowser.open(url)

    def _open_logs(self) -> None:
        log_file = self.manager.state_dir / "server.log"
        if not log_file.exists():
            log_file.write_text("Adjutant appliance log file initialized.\n", encoding="utf-8")
        if IS_WINDOWS:
            os.startfile(str(log_file))

    def run(self) -> None:
        """Run the Win32 message pump and background poller."""
        if not self.create_tray_window():
            logger.warning("Failed to initialize Windows tray icon. Running in console mode.")
            return

        if not self.no_browser:
            self._open_console()

        # Background status poller thread
        def _poll_loop() -> None:
            while self.manager.running:
                time.sleep(15)
                self.manager.poll_status()

        t = threading.Thread(target=_poll_loop, daemon=True)
        t.start()

        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # Cleanup tray icon
        if self.nid:
            shell32 = ctypes.windll.shell32
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))


def main() -> None:
    parser = argparse.ArgumentParser(description="Adjutant Desktop Appliance")
    parser.add_argument(
        "--web-port",
        "--port",
        dest="web_port",
        type=int,
        default=3000,
        help="Web Console frontend port (default: 3000)",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=8000,
        help="Core backend API port (default: 8000)",
    )
    parser.add_argument(
        "--db-port",
        type=int,
        default=55439,
        help="Local PostgreSQL port (default: 55439)",
    )
    parser.add_argument(
        "--state-directory",
        type=Path,
        default=ROOT / ".local" / "runner",
        help="Local state and storage directory",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the browser automatically upon launch",
    )
    args = parser.parse_args()

    state = args.state_directory.resolve()
    manager = ApplianceManager(
        web_port=args.web_port,
        api_port=args.api_port,
        db_port=args.db_port,
        state_dir=state,
    )

    try:
        success = manager.start_services()
        if not success:
            logger.error("Failed to launch Adjutant appliance services.")
            sys.exit(1)

        if IS_WINDOWS:
            tray = WindowsSystemTray(manager, no_browser=args.no_browser)
            tray.run()
        else:
            if not args.no_browser:
                webbrowser.open(f"http://127.0.0.1:{args.web_port}")
            logger.info("Adjutant running. Press Ctrl+C to terminate.")
            while manager.running:
                time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutdown initiated by keyboard interrupt.")
    finally:
        manager.stop_services()


if __name__ == "__main__":
    main()
