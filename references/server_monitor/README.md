# Server Monitor — Windows 11

A lightweight Python desktop overlay for monitoring HTTP/HTTPS endpoints and TCP ports.

## Features

- Transparent, frameless, always-on-top desktop overlay
- Compact view; hover to expand
- Green / red status indicators and latency
- Add, edit, and delete servers in the UI
- Persistent JSON configuration
- Per-server HTTP/HTTPS or TCP checks
- Background thread pool keeps network checks off the UI thread
- Adjustable polling interval and timeout
- Optional Windows startup entry
- Optional system-tray menu

## Requirements

- Windows 10/11
- Python 3.10 or newer
- Permission to install Python packages on the laptop

## Run from source

Open PowerShell in this folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

If PowerShell blocks activation, run the app with the environment's interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\main.py
```

## Configuration

The application creates its persistent configuration at:

```text
%APPDATA%\ServerMonitor\servers.json
```

Use the gear button, then **Add server** to manage entries. Use **Open JSON** to edit the file directly. Changes made externally to the JSON file are read on the next application launch; the app does not continuously watch for external edits.

Example:

```json
{
  "check_interval_seconds": 15,
  "timeout_seconds": 3,
  "start_with_windows": false,
  "servers": [
    {
      "name": "Internal health endpoint",
      "url": "https://internal.example.com/health",
      "type": "http"
    },
    {
      "name": "Build agent",
      "url": "build-agent.example.com:8080",
      "type": "tcp"
    }
  ]
}
```

### HTTP / HTTPS

- Enter a full URL, including `https://`.
- HTTP 200–399 is treated as online. HTTP 4xx/5xx is treated as offline/unhealthy.
- Prefer a dedicated health endpoint such as `/health` when available.
- Standard TLS certificate validation remains enabled.
- This implementation uses GET requests. Some endpoints may redirect to an authentication page or require credentials; use a health endpoint accessible to the current Windows user/network.

### TCP

Enter `hostname:port`, for example `build-agent.example.com:8080`. This verifies that a TCP connection can be opened, not that the application protocol is healthy.

## Status semantics

- **Green / ONLINE**: check succeeded.
- **Red / OFFLINE**: request failed, timed out, or returned an HTTP 4xx/5xx status.
- **Amber / UNKNOWN**: no completed check yet.

A network failure does not prove a server is powered off; VPN, DNS, firewall rules, proxies, or authentication can affect results.

## Desktop interaction

- Drag the widget by holding the left mouse button.
- Hover to expand; move the pointer away to collapse.
- Use the tray icon to show/hide, check all servers, open settings, or quit.
- The widget is always-on-top and intended for a normal desktop session, not the Windows lock screen.

## Optional startup

Enable **Start with Windows** in settings and save. This writes a small `.cmd` launcher in the current user's Startup folder. It uses the Python interpreter currently running the app. If you move the project or virtual environment, update the startup setting by turning it off and on again.

For a managed company laptop, follow your organization's software and endpoint policies. No administrator privileges should be required for a per-user install, but application-control policy may block Python packages or startup entries.

## Known limitations / next improvements

- Configuration is local and contains no credentials.
- No automatic external JSON file reload.
- The current implementation treats each server name as a unique key.
- The first run includes two example servers; remove or replace them.
- A packaged `.exe` can be built later with PyInstaller if permitted by company policy.
