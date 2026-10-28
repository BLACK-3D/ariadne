"""Explicit, bounded device-location reads; no tracking or automatic IP lookup.

The Windows provider may depend on Windows' own location services and consent.
This module never changes those settings, persists a position or claims that a
network-derived or browser-derived estimate is an exact physical location.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import _allowed_path


UTC = timezone.utc
MAX_TIMEOUT_SECONDS = 15
STALE_AFTER_SECONDS = 300
_DETECT_LOCK = threading.Lock()
WINDOWS_DOC = "https://learn.microsoft.com/en-us/dotnet/api/system.device.location.geocoordinatewatcher.trystart?view=netframework-4.8.1"
CENSUS_ST_LOUIS = "https://tigerweb.geo.census.gov/tigerwebmain/Files/acs25/tigerweb_acs25_incplace_mo.html"

# This is fixed application code. Caller-controlled text is never interpolated
# into a PowerShell command. The only stdin value is a validated time budget.
_WINDOWS_SCRIPT = r"""
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$watcher=$null
$result=$null
try {
  $inputData=[Console]::In.ReadToEnd() | ConvertFrom-Json
  $budget=[double]$inputData.timeout_seconds
  $elapsed=[Diagnostics.Stopwatch]::StartNew()
  Add-Type -AssemblyName System.Device
  $watcher=New-Object System.Device.Location.GeoCoordinateWatcher -ArgumentList ([System.Device.Location.GeoPositionAccuracy]::High)
  [void]$watcher.TryStart($false, [TimeSpan]::FromMilliseconds(1000))
  while ($watcher.Position.Location.IsUnknown -and $watcher.Permission -ne 'Denied' -and $watcher.Status -ne 'Disabled' -and $elapsed.Elapsed.TotalSeconds -lt $budget) {
    Start-Sleep -Milliseconds 100
  }
  if ($watcher.Permission -eq 'Denied') {
    $result=@{ok=$false;status='permission_denied';permission=[string]$watcher.Permission;provider_status=[string]$watcher.Status}
  } elseif ($watcher.Position.Location.IsUnknown) {
    $result=@{ok=$false;status='unavailable';permission=[string]$watcher.Permission;provider_status=[string]$watcher.Status}
  } else {
    $position=$watcher.Position
    $coordinate=$position.Location
    $accuracy=$coordinate.HorizontalAccuracy
    if ([Double]::IsNaN($accuracy) -or [Double]::IsInfinity($accuracy)) { $accuracy=$null }
    $result=@{ok=$true;latitude=$coordinate.Latitude;longitude=$coordinate.Longitude;accuracy_meters=$accuracy;measured_at_utc=$position.Timestamp.ToUniversalTime().ToString('o');permission=[string]$watcher.Permission;provider_status=[string]$watcher.Status}
  }
} catch {
  $result=@{ok=$false;status='unavailable';provider_status='error'}
} finally {
  if ($null -ne $watcher) {
    try { $watcher.Stop() } catch {}
    try { $watcher.Dispose() } catch {}
  }
}
[Console]::Out.Write(($result | ConvertTo-Json -Compress -Depth 3))
"""


def _now(value=None):
    value = value or datetime.now(UTC)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("The location clock must be timezone-aware.")
    return value.astimezone(UTC)


def _number(value, label, low, high):
    if type(value) not in {int, float} or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(label + " must be a finite number within its supported range.")
    return float(value)


def _result(latitude, longitude, accuracy, measured_at, source, fetched_at):
    latitude = _number(latitude, "Latitude", -90, 90)
    longitude = _number(longitude, "Longitude", -180, 180)
    if accuracy is not None:
        accuracy = _number(accuracy, "Horizontal accuracy", 0, 100_000_000)
    if not isinstance(measured_at, datetime) or measured_at.tzinfo is None:
        raise ValueError("The position measurement must include a timezone.")
    measured_at = measured_at.astimezone(UTC)
    age = (fetched_at-measured_at).total_seconds()
    if age < -300:
        raise ValueError("The location measurement time is unexpectedly in the future.")
    stale = age > STALE_AFTER_SECONDS
    return {"ok": True, "status": "available", "source": source, "device_location": True,
            "latitude": latitude, "longitude": longitude, "accuracy_meters": accuracy,
            "measured_at_utc": measured_at.isoformat(), "fetched_at_utc": fetched_at.isoformat(),
            "age_seconds": round(max(0, age), 3), "stale": stale, "exact": False,
            "note": ("This is a device/provider estimate, not an exact location. "
                     + ("The measurement is over five minutes old. " if stale else "")
                     + ("The provider did not report horizontal accuracy." if accuracy is None else "Horizontal accuracy is the provider's reported radius in meters."))}


def _unavailable(status, now, **extra):
    return {"ok": False, "status": status, "source": "windows_location_service", "device_location": False,
            "exact": False, "fetched_at_utc": now.isoformat(), **extra,
            "note": "Windows did not provide a device position. No location setting was changed and Ariadne did not call a public IP lookup service. Use the browser's explicit location request or choose a city reference point."}


def detect_windows_location(timeout_seconds=MAX_TIMEOUT_SECONDS, *, now=None):
    """Attempt one native Windows read after an explicit owner request.

    Hard process deadline is at most 15 seconds, including interpreter startup.
    No fallback city is substituted for an unavailable device measurement.
    """
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError("Location timeout must be an integer between 1 and 15 seconds.")
    fetched_at = _now(now)
    if os.name != "nt":
        return _unavailable("unsupported", fetched_at)
    if not _DETECT_LOCK.acquire(blocking=False):
        return _unavailable("busy", fetched_at)
    try:
        executable = _allowed_path(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        if not executable.is_file():
            return _unavailable("unavailable", fetched_at)
        try:
            completed = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_SCRIPT],
                                       input=json.dumps({"timeout_seconds": max(0.1, timeout_seconds-2)}).encode("ascii"),
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout_seconds,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return _unavailable("timeout", _now(now))
        except OSError:
            return _unavailable("unavailable", _now(now))
        fetched_at = _now(now)
        if completed.returncode or len(completed.stdout) > 8192:
            return _unavailable("unavailable", fetched_at)
        try:
            value = json.loads(completed.stdout.decode("utf-8-sig"))
            if type(value) is not dict or type(value.get("ok")) is not bool:
                raise ValueError("Invalid location provider result.")
            if not value["ok"]:
                status = "permission_denied" if value.get("status") == "permission_denied" else "unavailable"
                return _unavailable(status, fetched_at,
                                    permission=value.get("permission") if value.get("permission") in {"Granted", "Denied", "Unknown"} else "Unknown")
            measured_at = datetime.fromisoformat(value["measured_at_utc"])
            result = _result(value["latitude"], value["longitude"], value.get("accuracy_meters"),
                             measured_at, "windows_location_service", fetched_at)
            result["source_url"] = WINDOWS_DOC
            return result
        except (KeyError, TypeError, ValueError, UnicodeError, OverflowError):
            return _unavailable("unavailable", fetched_at)
    finally:
        _DETECT_LOCK.release()


def validate_browser_location(value, *, now=None):
    """Validate one consented browser Geolocation API result, without storage.

    Payload: latitude, longitude, accuracy_meters, timestamp_ms. Source labels
    are assigned here rather than accepting a caller's claim of exact GPS data.
    """
    if type(value) is not dict or set(value) != {"latitude", "longitude", "accuracy_meters", "timestamp_ms"}:
        raise ValueError("Expected latitude, longitude, accuracy_meters and timestamp_ms only.")
    fetched_at = _now(now)
    stamp = _number(value["timestamp_ms"], "Browser measurement time", 946_684_800_000, 253_402_300_799_000)
    measured_at = datetime.fromtimestamp(stamp/1000, UTC)
    if value["accuracy_meters"] is None:
        raise ValueError("Browser location must include its reported horizontal accuracy.")
    return _result(value["latitude"], value["longitude"], value["accuracy_meters"], measured_at,
                   "browser_device_geolocation", fetched_at)


def st_louis_reference():
    """Official city internal point; deliberately not a detected device position."""
    return {"ok": True, "status": "city_reference", "source": "census_city_reference", "device_location": False,
            "exact": False, "name": "St. Louis, MO", "country": "US", "latitude": 38.6356988,
            "longitude": -90.2445816, "accuracy_meters": None, "source_as_of": "2025-01-01",
            "source_url": CENSUS_ST_LOUIS, "geoid": "2965000", "coordinate_fields": ["INTPTLAT", "INTPTLON"],
            "note": "U.S. Census city reference point for regional forecasts. It is not this PC's location, a home address, or a measured device position."}
