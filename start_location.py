"""Initial map position from Windows Location, with IP geolocation as fallback."""

import json
import math
import subprocess
import sys
from urllib import request


WINDOWS_LOCATION_SCRIPT = r"""
chcp 65001 > $null
$ErrorActionPreference = 'Stop'
$geolocator = [Activator]::CreateInstance([Windows.Devices.Geolocation.Geolocator,Windows.Devices.Geolocation,ContentType=WindowsRuntime])
$geolocator.DesiredAccuracy = [Windows.Devices.Geolocation.PositionAccuracy]::High
$operation = $geolocator.GetGeopositionAsync()
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 } |
    Select-Object -First 1
$positionType = [Windows.Devices.Geolocation.Geoposition,Windows.Devices.Geolocation,ContentType=WindowsRuntime]
$task = $method.MakeGenericMethod($positionType).Invoke($null, @($operation))
if (-not $task.Wait(7000)) { exit 1 }
$point = $task.Result.Coordinate.Point.Position
[pscustomobject]@{
    latitude = $point.Latitude
    longitude = $point.Longitude
    accuracy = $task.Result.Coordinate.Accuracy
} | ConvertTo-Json -Compress
"""


def parse_point(data):
    point = (float(data["latitude"]), float(data["longitude"]))
    if not (math.isfinite(point[0]) and math.isfinite(point[1]) and
            -90 <= point[0] <= 90 and -180 <= point[1] <= 180):
        raise ValueError("Invalid location coordinates")
    return point


def get_windows_location():
    if sys.platform != "win32":
        raise OSError("Windows location unavailable")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-Command", WINDOWS_LOCATION_SCRIPT],
        capture_output=True, text=True, encoding="utf-8", timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=True,
    )
    data = json.loads(result.stdout)
    point = parse_point(data)
    accuracy = float(data["accuracy"])
    if not math.isfinite(accuracy) or accuracy < 0:
        raise ValueError("Invalid location accuracy")
    return point, "Windows", accuracy


def get_ip_location():
    req = request.Request("https://ipapi.co/json/", headers={"User-Agent": "BloomTraveler/1.1"})
    with request.urlopen(req, timeout=5) as response:
        data = json.load(response)
    if data.get("error"):
        raise ValueError("IP location lookup failed")
    return parse_point(data), "IP", None


def get_start_location():
    try:
        return get_windows_location()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return get_ip_location()
