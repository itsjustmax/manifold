"""Mesh v0: a gossiped directory of harbors.

Not a ledger, on purpose. A globally consistent list of all hosts is a
consensus problem nobody here needs to pay for; what an agent needs is
"the harbor I'm on knows other live harbors." So: each harbor keeps a
directory, periodically pulls its peers' directories, and — the rule
that keeps the mesh honest — VERIFIES A HARBOR IS ALIVE ITSELF before
ever re-sharing it. Dead tunnels age out; hearsay is probed before it
propagates; pinned peers (the operator's own peers.json) are vouched
and never pruned.

Directory only: careers do NOT merge across harbors here. That needs
the signature layer, or cross-harbor reputation is farmable.

Env knobs: MANIFOLD_MESH_INTERVAL (seconds, default 300),
MANIFOLD_MESH_ALLOW_LOCAL=1 (accept loopback/private peers — tests and
LAN parties only).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

MAX_PEERS = 200
PROBE_TIMEOUT = 4.0
PRUNE_AFTER_FAILS = 3


def origin_of(url: str) -> str | None:
    p = urlparse(str(url).strip())
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    return f"{p.scheme}://{p.netloc}".lower()


class Mesh:
    def __init__(self, data_dir: Path, instance_id: str):
        self.data = data_dir
        self.instance_id = instance_id
        self.interval = float(os.environ.get("MANIFOLD_MESH_INTERVAL", 300))
        self.allow_local = os.environ.get(
            "MANIFOLD_MESH_ALLOW_LOCAL", "") == "1"
        self.known: dict[str, dict] = self._load()

    # ------------------------------------------------------- persistence
    def _load(self) -> dict:
        p = self.data / "mesh.json"
        try:
            return json.loads(p.read_text()) if p.exists() else {}
        except json.JSONDecodeError:
            return {}

    def _save(self) -> None:
        (self.data / "mesh.json").write_text(json.dumps(self.known, indent=1))

    def pinned(self) -> list[dict]:
        p = self.data / "peers.json"
        if not p.exists():
            return []
        try:
            raw = json.loads(p.read_text()).get("peers", [])
        except json.JSONDecodeError:
            return []
        out = []
        for e in raw:
            o = origin_of(e.get("url", ""))
            if o:
                out.append({**e, "url": o})
        return out

    # ---------------------------------------------------------- security
    def _address_allowed(self, origin: str) -> bool:
        """Refuse private/loopback/link-local targets unless explicitly
        allowed — announce would otherwise be a probe-my-LAN service."""
        if self.allow_local:
            return True
        host = urlparse(origin).hostname or ""
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False
        for info in infos:
            try:
                ip = ipaddress.ip_address(info[4][0])
            except ValueError:
                return False
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast):
                return False
        return bool(infos)

    # ------------------------------------------------------------ probes
    def _get_json(self, url: str) -> dict | None:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "manifold-mesh/0.1")
        req.add_header("ngrok-skip-browser-warning", "1")
        try:
            with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as r:
                return json.loads(r.read().decode())
        except Exception:
            return None

    def _probe(self, origin: str) -> dict | None:
        """A live harbor answers /healthz with ok + games + instance."""
        d = self._get_json(f"{origin}/healthz")
        if not d or d.get("ok") is not True or "games" not in d:
            return None
        return d

    # --------------------------------------------------------- mutation
    def _note_alive(self, origin: str, health: dict, source: str) -> None:
        e = self.known.setdefault(origin, {
            "source": source, "first_seen_utc": _iso()})
        e.update(last_seen_utc=_iso(), fails=0,
                 games=sorted(health.get("games", []))[:12])

    def _note_dead(self, origin: str) -> None:
        e = self.known.get(origin)
        if e is None:
            return
        e["fails"] = e.get("fails", 0) + 1
        if e["fails"] >= PRUNE_AFTER_FAILS:
            del self.known[origin]

    async def announce(self, url: str) -> tuple[bool, int, str]:
        """A harbor knocked and asked to be listed. Verify before
        trusting: (accepted, http_status, reason)."""
        origin = origin_of(url)
        if origin is None:
            return False, 400, "url must be http(s)://host[:port]"
        if not self._address_allowed(origin):
            return False, 400, ("private or unresolvable addresses are not "
                                "listable on a public mesh")
        if origin in self.known:
            return True, 200, "already listed"
        if len(self.known) >= MAX_PEERS:
            return False, 429, "mesh directory full on this harbor"
        health = await asyncio.to_thread(self._probe, origin)
        if health is None:
            return False, 400, (f"probed {origin}/healthz and got no live "
                                "manifold harbor; announce only a reachable "
                                "public URL")
        if health.get("instance") == self.instance_id:
            return False, 400, "that is this harbor's own address"
        self._note_alive(origin, health, source="announce")
        self._save()
        return True, 200, "verified alive and listed"

    # ------------------------------------------------------------ gossip
    async def gossip_round(self) -> None:
        pinned_set = {p["url"] for p in self.pinned()}
        neighbors = list(pinned_set) + list(self.known.keys())
        # re-probe everyone we list; collect hearsay from their lists
        hearsay: list[str] = []
        for origin in dict.fromkeys(neighbors):
            health = await asyncio.to_thread(self._probe, origin)
            if health is None:
                self._note_dead(origin)
                continue
            if health.get("instance") == self.instance_id:
                self.known.pop(origin, None)   # someone listed us; skip self
                continue
            self._note_alive(origin, health,
                             "pinned" if origin in pinned_set
                             else self.known.get(origin, {}).get(
                                 "source", "gossip"))
            theirs = await asyncio.to_thread(
                self._get_json, f"{origin}/peers")
            for e in (theirs or {}).get("peers", []):
                o = origin_of(e.get("url", ""))
                if o and o not in self.known:
                    hearsay.append(o)
        # verify hearsay before it enters our directory
        for o in dict.fromkeys(hearsay):
            if len(self.known) >= MAX_PEERS:
                break
            if not self._address_allowed(o):
                continue
            health = await asyncio.to_thread(self._probe, o)
            if health and health.get("instance") != self.instance_id:
                self._note_alive(o, health, source="gossip")
        self._save()

    async def loop(self) -> None:
        await asyncio.sleep(min(5.0, self.interval))  # let uvicorn settle
        while True:
            try:
                await self.gossip_round()
            except Exception:
                pass                     # a bad round never kills the harbor
            await asyncio.sleep(self.interval)

    # ------------------------------------------------------------ output
    def listing(self) -> dict:
        pinned = self.pinned()
        pinned_urls = {p["url"] for p in pinned}
        out = [{**p, "source": "pinned",
                **{k: self.known[p["url"]][k]
                   for k in ("last_seen_utc", "games")
                   if p["url"] in self.known and k in self.known[p["url"]]}}
               for p in pinned]
        for url, e in self.known.items():
            if url in pinned_urls or e.get("source") == "pinned":
                continue
            out.append({"url": url, "source": e.get("source", "gossip"),
                        "last_seen_utc": e.get("last_seen_utc"),
                        "games": e.get("games", [])})
        return {"peers": out, "mesh": "v0-gossip",
                "note": ("directory only — careers do not transfer "
                         "between harbors until the signature layer")}


def _iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
