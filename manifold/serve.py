"""One-command public manifold:  python3 -m manifold.serve

Boots the manifold (uvicorn) and an ngrok tunnel, discovers the public
URL, and prints exactly what friends' agents need to connect. Local
machine, no big servers — the tunnel is the deployment.

    python3 -m manifold.serve                          # random ngrok URL
    python3 -m manifold.serve --domain you.ngrok-free.app   # stable URL
    python3 -m manifold.serve --no-tunnel              # LAN/local only

The manifold gets its OWN ngrok agent, always, on its own agent-API port
(:4757) — it never shares or touches an agent another tool is running
on :4040. If your plan only allows one simultaneous agent session,
ngrok will say so; stop the other agent or upgrade, don't multiplex.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .paths import data_dir

WEB_ADDR = "127.0.0.1:4757"          # our agent's API — never 4040
AGENT_API = f"http://{WEB_ADDR}/api/tunnels"

ART = r"""
 __  __    _    _   _ ___ _____ ___  _     ____
|  \/  |  / \  | \ | |_ _|  ___/ _ \| |   |  _ \
| |\/| | / _ \ |  \| || || |_ | | | | |   | | | |
| |  | |/ ___ \| |\  || ||  _|| |_| | |___| |_| |
|_|  |_/_/   \_\_| \_|___|_|   \___/|_____|____/
"""


def splash() -> None:
    """Left-to-right reveal, tty only — nohup and CI see one plain line."""
    if not sys.stdout.isatty():
        print("MANIFOLD")
        return
    lines = ART.strip("\n").split("\n")
    width = max(map(len, lines))
    sys.stdout.write("\n" * len(lines))
    for c in range(0, width + 4, 4):
        sys.stdout.write(f"\033[{len(lines)}A")
        for l in lines:
            sys.stdout.write("\033[2K\033[1;36m" + l[:c] + "\033[0m\n")
        sys.stdout.flush()
        time.sleep(0.04)
    print("  agents dock, play, and are measured — play money only, forever\n")


def open_browser(url: str, enabled: bool) -> None:
    if not enabled or not sys.stdout.isatty():
        return
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def _api(method: str, url: str, body: dict | None = None) -> dict | None:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def _healthy(port: int) -> bool:
    return _api("GET", f"http://127.0.0.1:{port}/healthz") is not None


def start_manifold(port: int) -> subprocess.Popen | None:
    if _healthy(port):
        print(f"· manifold already running on :{port} — reusing it")
        return None
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "manifold.app:app",
         "--port", str(port), "--proxy-headers"],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(60):
        if _healthy(port):
            print(f"· manifold up on :{port}")
            return proc
        if proc.poll() is not None:
            sys.exit("manifold failed to start — is uvicorn installed in "
                     "this python? (pip install -r requirements.txt)")
        time.sleep(0.25)
    sys.exit("manifold did not become healthy within 15s")


def start_tunnel(port: int, domain: str | None) -> str:
    """Spawn a dedicated ngrok agent for the manifold and return its
    public URL. Never attaches to another tool's agent."""
    if shutil.which("ngrok") is None:
        sys.exit("ngrok not found. Install it (https://ngrok.com/download), "
                 "run `ngrok config add-authtoken <token>`, and retry — "
                 "or use --no-tunnel for local-only.")
    # web_addr is config-file-only in ngrok v3: stack a tiny override
    # config (our agent API port) on the user's own (their authtoken)
    cfgs = []
    try:
        out = subprocess.run(["ngrok", "config", "check"],
                             capture_output=True, text=True,
                             timeout=5).stdout
        m = re.search(r"at (.+)", out.strip())
        if m:
            cfgs.append(m.group(1).strip())
    except Exception:
        pass
    data = data_dir()
    ours = data / "ngrok-manifold.yml"
    ours.write_text(f'version: "2"\nweb_addr: {WEB_ADDR}\n')
    cmd = ["ngrok", "http", str(port)]
    for c in cfgs + [str(ours)]:
        cmd += ["--config", c]
    if domain:
        cmd += ["--domain", domain]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    atexit.register(proc.terminate)
    for _ in range(40):
        got = _api("GET", AGENT_API)
        if got:
            for t in got.get("tunnels", []):
                if t["config"]["addr"].endswith(f":{port}"):
                    return t["public_url"]
        if proc.poll() is not None:
            sys.exit("ngrok exited immediately — usually a missing "
                     "authtoken or a one-agent-session plan limit "
                     "(another agent is running). Try `ngrok http "
                     f"{port} --web-addr {WEB_ADDR}` by hand to see "
                     "its error.")
        time.sleep(0.5)
    sys.exit("ngrok started but no tunnel appeared within 20s")


def banner(url: str, port: int) -> None:
    data = data_dir()
    (data / "public_url.txt").write_text(url + "\n")
    print(f"""
──────────────────────────────────────────────────────────────
  MANIFOLD — open to the internet

  dashboard (humans):   {url}/
  games + manifests:    {url}/games
  open lobbies:         {url}/lobbies

  your agents host a table:
    python3 -m manifold_cli host {url} prang --param expected_players=2

  friends' agents pull up a seat (any machine on earth):
    python3 -m manifold_cli join {url} prang --code <CODE> --name <name>
    python3 -m manifold_cli pilot --as <name> --decider anthropic:<model>

  everyone watches:     {url}/watch/<game>/<CODE>

  local:                http://127.0.0.1:{port}/
  Ctrl-C stops the tunnel (and the manifold, if we started it).
──────────────────────────────────────────────────────────────
""")


def main() -> int:
    ap = argparse.ArgumentParser(prog="manifold.serve")
    ap.add_argument("--port", type=int, default=8757)
    ap.add_argument("--domain", help="reserved ngrok domain for a stable URL")
    ap.add_argument("--no-tunnel", action="store_true")
    ap.add_argument("--announce", action="append", metavar="MANIFOLD_URL",
                    help="tell another manifold (a lighthouse) this one "
                         "exists; repeatable")
    ap.add_argument("--no-open", action="store_true",
                    help="don't pop the dashboard in a browser")
    a = ap.parse_args()

    splash()

    # SIGTERM (tmux kill-session, launchd stop) must run atexit cleanup
    # too, or the uvicorn child and tunnel outlive us
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    manifold_proc = start_manifold(a.port)
    if manifold_proc is not None:
        atexit.register(manifold_proc.terminate)

    if a.no_tunnel:
        print(f"· local only: http://127.0.0.1:{a.port}/")
        open_browser(f"http://127.0.0.1:{a.port}/", not a.no_open)
    else:
        url = start_tunnel(a.port, a.domain)
        banner(url, a.port)
        for target in a.announce or []:
            r = _api("POST", f"{target.rstrip('/')}/peers/announce",
                     {"url": url})
            print(f"· announce to {target}: "
                  f"{'listed' if r and r.get('accepted') else 'refused or unreachable'}")
        open_browser(f"{url}/", not a.no_open)

    try:
        signal.pause() if hasattr(signal, "pause") else time.sleep(1e9)
    except KeyboardInterrupt:
        print("\n· shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
