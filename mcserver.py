"""Interaction with the Minecraft server running in a tmux session."""

import logging
import os
import re
import subprocess
import time

logger = logging.getLogger(__name__)

TMUX_SESSION = os.getenv("TMUX_SESSION", "srv")


def send_server_command(command):
    """Send a command to the Minecraft server console in tmux and capture the pane output."""
    subprocess.run(
        ["tmux", "send-keys", "-t", TMUX_SESSION, command, "Enter"],
        check=True,
        capture_output=True,
    )
    time.sleep(1)
    return capture_pane()


def capture_pane(lines=2000):
    """Capture the last `lines` lines of the tmux pane as text."""
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", TMUX_SESSION, "-p", "-S", f"-{lines}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def get_tps():
    """Run 'tps' on the Minecraft server console and parse its output."""
    output = send_server_command("tps")
    match = re.search(
        r"TPS from last 1m, 5m, 15m:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)",
        output,
    )
    if not match:
        return None
    return tuple(float(value) for value in match.groups())


def get_online_players():
    """Run 'list' on the Minecraft server console and parse its output."""
    output = send_server_command("list")
    match = re.search(
        r"There are (\d+) of a max of (\d+) players online: ?(.*)",
        output,
    )
    if not match:
        return None
    online, max_players, names = match.groups()
    names = names.strip()
    if names.endswith("playing"):
        names = names[: -len("playing")].strip()
    return int(online), int(max_players), names


def get_server_uptime():
    """Return the Minecraft server (java process) uptime in seconds, or None."""
    pid = _find_java_pid()
    if not pid:
        return None
    result = subprocess.run(
        ["ps", "-o", "etime=", "-p", pid],
        check=False,
        capture_output=True,
        text=True,
    )
    etime = result.stdout.strip()
    if not etime:
        return None
    # etime formats: [[DD-]hh:]mm:ss
    total_seconds = 0
    if "-" in etime:
        days, etime = etime.split("-", 1)
        total_seconds += int(days) * 86400
    parts = etime.split(":")
    if len(parts) == 3:
        total_seconds += int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    elif len(parts) == 2:
        total_seconds += int(parts[0]) * 60 + int(parts[1])
    return total_seconds


def _find_java_pid():
    """Return the PID of the running java process, or None."""
    result = subprocess.run(
        ["pgrep", "-x", "java"],
        check=False,
        capture_output=True,
        text=True,
    )
    pids = result.stdout.split()
    return pids[0] if pids else None


def _get_server_cwd():
    """Return the working directory of the Minecraft server process, or None."""
    pid = _find_java_pid()
    if not pid:
        return None
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        logger.warning("Could not read the server working directory", exc_info=True)
        return None


def get_mc_version():
    """Return the Minecraft version, or None if it cannot be determined."""
    # 1. Try the startup line in the tmux pane history
    try:
        output = capture_pane()
        match = re.search(r"Starting minecraft server version (\d[\d.]+)", output)
        if match:
            return match.group(1)
    except (FileNotFoundError, subprocess.CalledProcessError):
        logger.warning("Could not capture tmux pane", exc_info=True)
    # 2. Fall back to the server's log file in its working directory
    cwd = _get_server_cwd()
    if not cwd:
        return None
    try:
        with open(os.path.join(cwd, "logs", "latest.log")) as f:
            for _ in range(200):  # startup lines are near the top
                line = f.readline()
                if not line:
                    break
                match = re.search(r"Starting minecraft server version (\d[\d.]+)", line)
                if match:
                    return match.group(1)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Could not read server log", exc_info=True)
    return None
