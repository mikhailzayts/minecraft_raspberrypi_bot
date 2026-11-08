"""System information for the Raspberry Pi hosting the bot."""

import logging
import os
import re
import socket
import subprocess
import urllib.request

logger = logging.getLogger(__name__)

THROTTLED_FLAGS = [
    (0x1, "Пониженное напряжение сейчас"),
    (0x2, "Частота CPU ограничена сейчас"),
    (0x4, "Троттлинг сейчас"),
    (0x10000, "Происходило пониженное напряжение"),
    (0x20000, "Происходило ограничение частоты CPU"),
    (0x40000, "Происходил троттлинг"),
]

_public_ip_cache = None


def format_uptime(seconds):
    """Format an uptime duration in seconds as '2d 4h 15m'."""
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def format_optional(value, text):
    """Return text, or 'N/A' if the value is missing."""
    return text if value is not None else "N/A"


def read_file_float(path, scale=1.0):
    """Read a numeric value from a file and scale it, or return None."""
    try:
        with open(path) as f:
            return float(f.read().split()[0]) / scale
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Could not read %s", path, exc_info=True)
        return None


def get_cpu_temp_c():
    """Return CPU temperature in °C via 'vcgencmd measure_temp', or None if it cannot be read."""
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        match = re.search(r"temp=([\d.]+)'?C", result.stdout)
        if match:
            return float(match.group(1))
    except FileNotFoundError:
        # vcgencmd is not installed (e.g. not a Raspberry Pi) - use the fallback
        pass
    except (OSError, ValueError):
        logger.warning("Could not read temperature via vcgencmd", exc_info=True)
    # Fallback: read the value directly from the kernel
    temp = read_file_float("/sys/class/thermal/thermal_zone0/temp", scale=1000)
    if temp is None:
        logger.info("Temperature is not available on this system")
    return temp


def get_throttled():
    """Return the 'vcgencmd get_throttled' value as an int, or None if it cannot be read."""
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        match = re.search(r"throttled=0x([0-9a-fA-F]+)", result.stdout)
        if match:
            return int(match.group(1), 16)
    except FileNotFoundError:
        # vcgencmd is not installed (e.g. not a Raspberry Pi)
        return None
    except (OSError, ValueError):
        logger.warning("Could not read throttling status", exc_info=True)
    return None


def get_pi_stats():
    """Collect CPU load, RAM usage, temperature, throttling and uptime of the Raspberry Pi.

    Individual values that cannot be read are returned as None instead of raising.
    """
    stats = {
        "load_1": None,
        "load_5": None,
        "load_15": None,
        "used_kb": None,
        "total_kb": None,
        "mem_percent": None,
        "temp_c": None,
        "throttled": None,
        "uptime_seconds": None,
    }
    try:
        load_1, load_5, load_15 = os.getloadavg()
        stats.update(load_1=load_1, load_5=load_5, load_15=load_15)
    except OSError:
        logger.warning("Could not read load averages", exc_info=True)

    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, value = line.split(":", 1)
                meminfo[key] = int(value.strip().split()[0])  # kB
        total_kb = meminfo.get("MemTotal", 0)
        available_kb = meminfo.get("MemAvailable", 0)
        used_kb = total_kb - available_kb
        stats["total_kb"] = total_kb
        stats["used_kb"] = used_kb
        stats["mem_percent"] = used_kb / total_kb * 100 if total_kb else 0.0
    except (OSError, ValueError):
        logger.warning("Could not read memory usage", exc_info=True)

    stats["temp_c"] = get_cpu_temp_c()
    stats["throttled"] = get_throttled()

    stats["uptime_seconds"] = read_file_float("/proc/uptime")

    return stats


def get_system_uptime():
    """Return the system uptime in seconds, or None if it cannot be read."""
    return read_file_float("/proc/uptime")


def get_local_ip():
    """Return the local (LAN) IP address, or None if it cannot be determined."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0)
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def get_public_ip():
    """Return the public IP address, or None if it cannot be fetched. The result is cached."""
    global _public_ip_cache
    if _public_ip_cache:
        return _public_ip_cache
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as response:
            _public_ip_cache = response.read().decode().strip()
    except OSError as error:
        logger.warning("Could not fetch public IP: %s", error)
        return None
    return _public_ip_cache
