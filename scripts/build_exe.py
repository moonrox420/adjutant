"""Standalone Packaging Automation for Adjutant Windows Desktop Appliance.

Uses PyInstaller to build a native Windows .exe (adjutant.exe) with custom icon,
bundled migrations, static assets, and system tray orchestration.
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("build_exe")


def ensure_icon(icon_path: Path) -> Path:
    """Ensure brand icon exists; generate with PIL if absent."""
    if icon_path.exists():
        return icon_path
    try:
        from PIL import Image, ImageDraw

        icon_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGBA", (64, 64), color=(0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Rounded rectangle background in Adjutant pine green
        draw.rounded_rectangle([4, 4, 60, 60], radius=12, fill=(34, 88, 70, 255))
        # Elegant gold upward diagonal chevron
        draw.line([(22, 42), (42, 22)], fill=(216, 232, 164, 255), width=6)
        draw.line([(28, 22), (42, 22)], fill=(216, 232, 164, 255), width=6)
        draw.line([(42, 22), (42, 36)], fill=(216, 232, 164, 255), width=6)
        img.save(icon_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
        logger.info("Generated brand icon at %s", icon_path)
    except Exception as exc:
        logger.warning("Could not generate icon: %s", exc)
    return icon_path


def build_console() -> bool:
    """Compile the Next.js web console production bundle."""
    web_dir = ROOT / "web"
    if not (web_dir / "package.json").exists():
        logger.warning("No web/package.json found. Skipping web build.")
        return True

    logger.info("Building Next.js production console bundle...")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        logger.warning("npm binary not found on PATH; skipping frontend compilation.")
        return False

    cmd = [npm, "run", "build"]
    result = subprocess.run(cmd, cwd=web_dir)
    return result.returncode == 0


def generate_spec(
    spec_path: Path,
    onefile: bool = False,
    icon_path: Path | None = None,
) -> None:
    """Generate PyInstaller .spec file with all required datas and hidden imports."""
    icon_entry = f"r'{icon_path.resolve()}'" if icon_path and icon_path.exists() else "None"
    entry_script = (ROOT / "scripts" / "desktop.py").resolve()
    src_dir = (ROOT / "src").resolve()
    migrations_dir = (ROOT / "migrations").resolve()
    event_registry = (ROOT / "src" / "adjutant" / "event_registry.json").resolve()
    scripts_dir = (ROOT / "scripts").resolve()
    hooks_dir = (ROOT / "hooks").resolve()
    venv_site_packages = (ROOT / ".venv" / "Lib" / "site-packages").resolve()

    spec_content = f"""# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

added_files = [
    (r'{event_registry}', 'adjutant'),
    (r'{migrations_dir}', 'migrations'),
    (r'{scripts_dir}', 'scripts'),
]

hidden_imports = [
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'psycopg',
    'psycopg.pool',
    'psycopg_pool',
    'psycopg_binary',
    'cryptography',
    'pydantic_settings',
    'email_validator',
    'adjutant.licensing',
    'adjutant.licensing_api',
    'adjutant.api',
    'adjutant.runner',
    'adjutant.models',
    'adjutant.events',
]

a = Analysis(
    [r'{entry_script}'],
    pathex=[r'{src_dir}', r'{ROOT}', r'{venv_site_packages}'],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[r'{hooks_dir}'],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[
        'numpy',
        'scipy',
        'pandas',
        'matplotlib',
        'pytest',
        'unittest',
        'tkinter',
        'IPython',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

{"exe = EXE(" if onefile else "exe = EXE("}
    pyz,
    a.scripts,
    {"a.binaries, a.zipfiles, a.datas," if onefile else ""}
    [],
    exclude_binaries={"False" if onefile else "True"},
    name='adjutant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon={icon_entry},
)

{
        "# Single file build complete"
        if onefile
        else '''coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='adjutant',
)'''
    }
"""
    spec_path.write_text(spec_content, encoding="utf-8")
    logger.info("Wrote PyInstaller spec to %s", spec_path)


def run_pyinstaller(spec_path: Path, dist_dir: Path, work_dir: Path) -> bool:
    """Execute PyInstaller on the generated spec file."""
    # Check in active python virtual environment Scripts directory first
    venv_pyinstaller = Path(sys.executable).parent / (
        "pyinstaller.exe" if os.name == "nt" else "pyinstaller"
    )
    if venv_pyinstaller.exists():
        pyinstaller = str(venv_pyinstaller)
    else:
        pyinstaller = shutil.which("pyinstaller")

    if not pyinstaller:
        logger.error(
            "PyInstaller is not installed in the environment. "
            "Install it using: pip install pyinstaller"
        )
        return False

    venv_site_packages = (ROOT / ".venv" / "Lib" / "site-packages").resolve()
    sep = ";" if os.name == "nt" else ":"
    env = dict(
        os.environ,
        PYTHONPATH=f"{ROOT / 'src'}{sep}{ROOT}{sep}{venv_site_packages}",
    )
    cmd = [
        pyinstaller,
        "--noconfirm",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        str(spec_path),
    ]
    logger.info("Executing PyInstaller: %s", " ".join(cmd))
    res = subprocess.run(cmd, cwd=ROOT, env=env)
    return res.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Adjutant Windows Desktop Executable")
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Package as a single monolithic .exe file",
    )
    parser.add_argument(
        "--skip-web",
        action="store_true",
        help="Skip rebuilding the Next.js frontend console",
    )
    args = parser.parse_args()

    icon_path = ensure_icon(ROOT / "assets" / "app_icon.ico")

    if not args.skip_web:
        console_ok = build_console()
        if not console_ok:
            logger.warning("Console build returned non-zero code. Continuing packaging...")

    dist_dir = ROOT / "dist"
    build_dir = ROOT / "build"
    spec_path = ROOT / "adjutant.spec"

    dist_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)

    generate_spec(spec_path, onefile=args.onefile, icon_path=icon_path)
    success = run_pyinstaller(spec_path, dist_dir, build_dir)

    if success:
        exe_location = (
            dist_dir / "adjutant.exe" if args.onefile else dist_dir / "adjutant" / "adjutant.exe"
        )
        logger.info("Build completed successfully!")
        logger.info("Standalone executable location: %s", exe_location)
    else:
        logger.error("Build failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
