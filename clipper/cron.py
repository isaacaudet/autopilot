"""Cron/launchd setup for automated release publishing."""

import platform
import subprocess
import sys
from pathlib import Path


PLIST_NAME = "com.clipper.release"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_NAME}.plist"
CRON_MARKER = "# clipper-release"

AUTOPILOT_PLIST_NAME = "com.clipper.autopilot"
AUTOPILOT_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{AUTOPILOT_PLIST_NAME}.plist"
AUTOPILOT_CRON_MARKER = "# clipper-autopilot"
AUTOPILOT_LOG = Path.home() / ".clipper_autopilot.log"
AUTOPILOT_TIMES = [
    {"Hour": 7, "Minute": 0},
    {"Hour": 12, "Minute": 0},
    {"Hour": 17, "Minute": 0},
]

CROSSPOST_PLIST_NAME = "com.clipper.crosspost"
CROSSPOST_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{CROSSPOST_PLIST_NAME}.plist"
CROSSPOST_CRON_MARKER = "# clipper-crosspost"
CROSSPOST_LOG = Path.home() / ".clipper_crosspost.log"

MARATHON_PLIST_NAME = "com.clipper.marathon"
MARATHON_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{MARATHON_PLIST_NAME}.plist"
MARATHON_CRON_MARKER = "# clipper-marathon"
MARATHON_LOG = Path.home() / ".clipper_marathon.log"


def detect_platform() -> str:
    """Return 'macos' or 'linux'."""
    return "macos" if platform.system() == "Darwin" else "linux"


def _clipper_bin() -> str:
    """Get the path to the clipper executable."""
    # Use sys.executable to find the Python that has clipper installed
    return f"{sys.executable} -m clipper"


def is_installed() -> bool:
    """Check if cron/launchd job is installed."""
    if detect_platform() == "macos":
        return PLIST_PATH.exists()
    else:
        try:
            result = subprocess.run(
                ["crontab", "-l"], capture_output=True, text=True, timeout=5
            )
            return CRON_MARKER in result.stdout
        except (subprocess.SubprocessError, FileNotFoundError):
            return False


def install() -> bool:
    """Install cron/launchd job. Returns True on success."""
    cmd = _clipper_bin()

    if detect_platform() == "macos":
        project_dir = str(Path(__file__).parent.parent.resolve())
        pyenv_bin = str(Path(sys.executable).parent)
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_NAME}</string>
    <key>WorkingDirectory</key>
    <string>{project_dir}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{pyenv_bin}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string>
        <string>clipper</string>
        <string>release</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>StandardOutPath</key>
    <string>{Path.home() / '.clipper_release.log'}</string>
    <key>StandardErrorPath</key>
    <string>{Path.home() / '.clipper_release.log'}</string>
</dict>
</plist>"""
        PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        PLIST_PATH.write_text(plist_content)
        result = subprocess.run(
            ["launchctl", "load", str(PLIST_PATH)],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    else:
        # Linux: add to crontab
        try:
            result = subprocess.run(
                ["crontab", "-l"], capture_output=True, text=True, timeout=5
            )
            current = result.stdout if result.returncode == 0 else ""
            if CRON_MARKER in current:
                return True  # Already installed
            new_line = f"*/5 * * * * {cmd} release {CRON_MARKER}\n"
            new_crontab = current.rstrip("\n") + "\n" + new_line
            proc = subprocess.run(
                ["crontab", "-"], input=new_crontab, text=True,
                capture_output=True, timeout=10
            )
            return proc.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False


def remove() -> bool:
    """Remove cron/launchd job. Returns True on success."""
    if detect_platform() == "macos":
        if not PLIST_PATH.exists():
            return True
        subprocess.run(
            ["launchctl", "unload", str(PLIST_PATH)],
            capture_output=True, timeout=10
        )
        PLIST_PATH.unlink(missing_ok=True)
        return True
    else:
        try:
            result = subprocess.run(
                ["crontab", "-l"], capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return True
            lines = [l for l in result.stdout.splitlines() if CRON_MARKER not in l]
            new_crontab = "\n".join(lines) + "\n"
            proc = subprocess.run(
                ["crontab", "-"], input=new_crontab, text=True,
                capture_output=True, timeout=10
            )
            return proc.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False


def is_autopilot_installed() -> bool:
    """Check if autopilot cron/launchd job is installed."""
    if detect_platform() == "macos":
        return AUTOPILOT_PLIST_PATH.exists()
    else:
        try:
            result = subprocess.run(
                ["crontab", "-l"], capture_output=True, text=True, timeout=5
            )
            return AUTOPILOT_CRON_MARKER in result.stdout
        except (subprocess.SubprocessError, FileNotFoundError):
            return False


def install_autopilot() -> bool:
    """Install autopilot cron/launchd job (runs 3x/day). Returns True on success."""
    if detect_platform() == "macos":
        project_dir = str(Path(__file__).parent.parent.resolve())
        pyenv_bin = str(Path(sys.executable).parent)

        # Build StartCalendarInterval array for multiple run times
        interval_entries = "\n".join(
            f"""        <dict>
            <key>Hour</key>
            <integer>{t['Hour']}</integer>
            <key>Minute</key>
            <integer>{t['Minute']}</integer>
        </dict>"""
            for t in AUTOPILOT_TIMES
        )

        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{AUTOPILOT_PLIST_NAME}</string>
    <key>WorkingDirectory</key>
    <string>{project_dir}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{pyenv_bin}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string>
        <string>clipper</string>
        <string>daily-compilation</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
{interval_entries}
    </array>
    <key>StandardOutPath</key>
    <string>{AUTOPILOT_LOG}</string>
    <key>StandardErrorPath</key>
    <string>{AUTOPILOT_LOG}</string>
</dict>
</plist>"""
        AUTOPILOT_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUTOPILOT_PLIST_PATH.write_text(plist_content)
        result = subprocess.run(
            ["launchctl", "load", str(AUTOPILOT_PLIST_PATH)],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    else:
        # Linux: add 3 crontab entries
        try:
            result = subprocess.run(
                ["crontab", "-l"], capture_output=True, text=True, timeout=5
            )
            current = result.stdout if result.returncode == 0 else ""
            if AUTOPILOT_CRON_MARKER in current:
                return True
            cmd = _clipper_bin()
            new_lines = "\n".join(
                f"{t['Minute']} {t['Hour']} * * * {cmd} autopilot {AUTOPILOT_CRON_MARKER}"
                for t in AUTOPILOT_TIMES
            )
            new_crontab = current.rstrip("\n") + "\n" + new_lines + "\n"
            proc = subprocess.run(
                ["crontab", "-"], input=new_crontab, text=True,
                capture_output=True, timeout=10
            )
            return proc.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False


def remove_autopilot() -> bool:
    """Remove autopilot cron/launchd job. Returns True on success."""
    if detect_platform() == "macos":
        if not AUTOPILOT_PLIST_PATH.exists():
            return True
        subprocess.run(
            ["launchctl", "unload", str(AUTOPILOT_PLIST_PATH)],
            capture_output=True, timeout=10
        )
        AUTOPILOT_PLIST_PATH.unlink(missing_ok=True)
        return True
    else:
        try:
            result = subprocess.run(
                ["crontab", "-l"], capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return True
            lines = [l for l in result.stdout.splitlines() if AUTOPILOT_CRON_MARKER not in l]
            new_crontab = "\n".join(lines) + "\n"
            proc = subprocess.run(
                ["crontab", "-"], input=new_crontab, text=True,
                capture_output=True, timeout=10
            )
            return proc.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False


def install_crosspost() -> bool:
    """Install daily cross-post launchd job (runs at 9am). Returns True on success."""
    project_dir = str(Path(__file__).parent.parent.resolve())
    pyenv_bin = str(Path(sys.executable).parent)

    if detect_platform() == "macos":
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{CROSSPOST_PLIST_NAME}</string>
    <key>WorkingDirectory</key>
    <string>{project_dir}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{pyenv_bin}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string>
        <string>clipper.crosspost</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{CROSSPOST_LOG}</string>
    <key>StandardErrorPath</key>
    <string>{CROSSPOST_LOG}</string>
</dict>
</plist>"""
        CROSSPOST_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        CROSSPOST_PLIST_PATH.write_text(plist_content)
        result = subprocess.run(
            ["launchctl", "load", str(CROSSPOST_PLIST_PATH)],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    else:
        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
            current = result.stdout if result.returncode == 0 else ""
            if CROSSPOST_CRON_MARKER in current:
                return True
            cmd = _clipper_bin()
            new_line = f"0 9 * * * cd {project_dir} && {cmd.split()[0]} -m clipper.crosspost {CROSSPOST_CRON_MARKER}\n"
            proc = subprocess.run(
                ["crontab", "-"], input=current.rstrip("\n") + "\n" + new_line,
                text=True, capture_output=True, timeout=10,
            )
            return proc.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False


def remove_crosspost() -> bool:
    """Remove cross-post launchd job."""
    if detect_platform() == "macos":
        if not CROSSPOST_PLIST_PATH.exists():
            return True
        subprocess.run(["launchctl", "unload", str(CROSSPOST_PLIST_PATH)], capture_output=True, timeout=10)
        CROSSPOST_PLIST_PATH.unlink(missing_ok=True)
        return True
    else:
        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                return True
            lines = [l for l in result.stdout.splitlines() if CROSSPOST_CRON_MARKER not in l]
            subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, capture_output=True, timeout=10)
            return True
        except (subprocess.SubprocessError, FileNotFoundError):
            return False


def is_marathon_installed() -> bool:
    """Check if Marathon daily-compilation launchd job is installed."""
    if detect_platform() == "macos":
        return MARATHON_PLIST_PATH.exists()
    else:
        try:
            result = subprocess.run(
                ["crontab", "-l"], capture_output=True, text=True, timeout=5
            )
            return MARATHON_CRON_MARKER in result.stdout
        except (subprocess.SubprocessError, FileNotFoundError):
            return False


MARATHON_TIMES = [
    {"Hour": 8, "Minute": 0},
    {"Hour": 13, "Minute": 0},
    {"Hour": 18, "Minute": 0},
]


def install_marathon() -> bool:
    """Install Marathon daily-compilation launchd job (runs 3x/day). Returns True on success."""
    project_dir = str(Path(__file__).parent.parent.resolve())
    pyenv_bin = str(Path(sys.executable).parent)

    if detect_platform() == "macos":
        marathon_intervals = "\n".join(
            f"""        <dict>
            <key>Hour</key>
            <integer>{t['Hour']}</integer>
            <key>Minute</key>
            <integer>{t['Minute']}</integer>
        </dict>"""
            for t in MARATHON_TIMES
        )

        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{MARATHON_PLIST_NAME}</string>
    <key>WorkingDirectory</key>
    <string>{project_dir}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{pyenv_bin}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string>
        <string>clipper</string>
        <string>daily-compilation</string>
        <string>--channel</string>
        <string>class_instantiate</string>
        <string>--game</string>
        <string>Marathon</string>
        <string>--privacy</string>
        <string>public</string>
        <string>--layout</string>
        <string>blur</string>
        <string>--scope</string>
        <string>gamewide</string>
        <string>--min-score</string>
        <string>50</string>
        <string>--period</string>
        <string>7d</string>
        <string>--upload-channels</string>
        <string>instagram_marathon,tiktok_marathon</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
{marathon_intervals}
    </array>
    <key>StandardOutPath</key>
    <string>{MARATHON_LOG}</string>
    <key>StandardErrorPath</key>
    <string>{MARATHON_LOG}</string>
</dict>
</plist>"""
        MARATHON_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MARATHON_PLIST_PATH.write_text(plist_content)
        result = subprocess.run(
            ["launchctl", "load", str(MARATHON_PLIST_PATH)],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    else:
        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
            current = result.stdout if result.returncode == 0 else ""
            if MARATHON_CRON_MARKER in current:
                return True
            cmd = _clipper_bin()
            new_line = (
                f"0 9 * * * cd {project_dir} && {cmd} daily-compilation "
                f"--channel class_instantiate --game Marathon --privacy public --layout blur --scope gamewide "
                f"--min-score 50 --period 7d --upload-channels instagram_marathon,tiktok_marathon "
                f"{MARATHON_CRON_MARKER}\n"
            )
            proc = subprocess.run(
                ["crontab", "-"], input=current.rstrip("\n") + "\n" + new_line,
                text=True, capture_output=True, timeout=10,
            )
            return proc.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False


def remove_marathon() -> bool:
    """Remove Marathon launchd job."""
    if detect_platform() == "macos":
        if not MARATHON_PLIST_PATH.exists():
            return True
        subprocess.run(["launchctl", "unload", str(MARATHON_PLIST_PATH)], capture_output=True, timeout=10)
        MARATHON_PLIST_PATH.unlink(missing_ok=True)
        return True
    else:
        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                return True
            lines = [l for l in result.stdout.splitlines() if MARATHON_CRON_MARKER not in l]
            subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, capture_output=True, timeout=10)
            return True
        except (subprocess.SubprocessError, FileNotFoundError):
            return False


def get_status() -> dict:
    """Return cron status dict for both release and autopilot jobs."""
    times_str = ", ".join(f"{t['Hour']:02d}:{t['Minute']:02d}" for t in AUTOPILOT_TIMES)
    return {
        "installed": is_installed(),
        "release_installed": is_installed(),
        "platform": detect_platform(),
        "interval": "5 min",
        "command": "clipper release",
        "autopilot_installed": is_autopilot_installed(),
        "autopilot_schedule": times_str,
        "autopilot_command": "clipper autopilot",
        "marathon_installed": is_marathon_installed(),
        "marathon_schedule": ", ".join(f"{t['Hour']:02d}:{t['Minute']:02d}" for t in MARATHON_TIMES),
        "marathon_command": "clipper daily-compilation --channel class_instantiate --game Marathon --privacy public --layout blur --scope gamewide --min-score 50 --period 7d --upload-channels instagram_marathon,tiktok_marathon",
        "crosspost_installed": CROSSPOST_PLIST_PATH.exists() if detect_platform() == "macos" else False,
    }
