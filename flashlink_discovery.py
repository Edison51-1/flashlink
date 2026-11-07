"""FlashLink - détection automatique des appareils (plus besoin de saisir une adresse IP).

Principe : chaque appareil « visible » envoie un petit message UDP toutes les 2 secondes
(son nom, son port de transfert). Les autres appareils l'écoutent et construisent la liste.
La détection marche dans les deux sens : quand un appareil nous annonce, on lui répond directement,
et on continue de lui écrire directement. Ainsi, même un PC qui partage un point d'accès Wi-Fi
(plusieurs réseaux sur la même machine) est vu par les autres.
Aucune dépendance : bibliothèque standard Python uniquement (Windows et Linux).
"""
import json
import socket
import struct
import threading
import time
import uuid

DISCOVERY_PORT = 47474          # port UDP de détection (le même sur tous les appareils)
MCAST_GROUP = "239.255.42.99"   # groupe multicast (en plus du broadcast)
INTERVAL = 2.0                  # secondes entre deux annonces
TIMEOUT = 7.0                   # un appareil silencieux disparaît après ce délai
APP = "flashlink"
VERSION = 1


def _linux_interfaces():
    """(ip, broadcast) de chaque interface réseau sous Linux, y compris celle du point d'accès."""
    out = []
    try:
        import fcntl
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for _, name in socket.if_nameindex():
            ifr = struct.pack("256s", name.encode()[:15])
            try:
                ip = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, ifr)[20:24])   # SIOCGIFADDR
                bc = socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8919, ifr)[20:24])   # SIOCGIFBRDADDR
            except OSError:
                continue
            out.append((ip, bc))
        s.close()
    except (ImportError, AttributeError, OSError):
        pass
    return out


def _interfaces():
    """Liste des (ip, adresse_broadcast) de la machine, toutes interfaces confondues, sans le loopback."""
    found = {}

    def add(ip, bc=None):
        if ip.startswith("127.") or ip.startswith("0."):
            return
        if not bc or bc == "0.0.0.0":
            bc = ip.rsplit(".", 1)[0] + ".255"
        found.setdefault(ip, bc)

    try:
        import psutil  # optionnel : donne les vrais masques réseau
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET:
                    add(a.address, a.broadcast)
    except Exception:
        pass
    for ip, bc in _linux_interfaces():
        add(ip, bc)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # n'envoie rien : sert à trouver l'interface par défaut
        add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return list(found.items())


class Discovery:
    def __init__(self, name, tcp_port, platform="pc", on_change=None,
                 extra_targets=(), port=DISCOVERY_PORT, device_id=None, bind_ip=""):
        self.id = device_id or uuid.uuid4().hex[:12]
        self.name, self.tcp_port, self.platform = name, tcp_port, platform
        self.on_change, self.extra_targets, self.port = on_change, list(extra_targets), port
        self.bind_ip = bind_ip
        self.visible = True
        self._devices, self._lock, self._stop = {}, threading.Lock(), threading.Event()
        self._replied = {}

    # ---- API publique -------------------------------------------------
    def start(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        rx.bind((self.bind_ip, self.port))
        try:
            mreq = struct.pack("4s4s", socket.inet_aton(MCAST_GROUP), socket.inet_aton("0.0.0.0"))
            rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            pass  # le broadcast suffit si le multicast est indisponible
        rx.settimeout(1.0)
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        if self.bind_ip:
            tx.bind((self.bind_ip, 0))
        self._rx, self._tx = rx, tx
        for fn in (self._listen, self._announce):
            threading.Thread(target=fn, daemon=True).start()

    def stop(self):
        self._send("bye")
        self._stop.set()
        for s in (self._rx, self._tx):
            try:
                s.close()
            except OSError:
                pass

    def set_visible(self, visible):
        self.visible = visible
        if not visible:
            self._send("bye")

    def devices(self):
        with self._lock:
            return sorted(self._devices.values(), key=lambda d: d["name"].lower())

    # ---- interne ------------------------------------------------------
    def _message(self, kind):
        return json.dumps({"app": APP, "v": VERSION, "t": kind, "id": self.id,
                           "name": self.name, "port": self.tcp_port,
                           "platform": self.platform}).encode()

    def _send(self, kind):
        """Annonce sur tous les réseaux, et directement à chaque appareil déjà connu."""
        data = self._message(kind)
        with self._lock:
            known = {ip for d in self._devices.values() for ip in d.get("ips", {})}
        targets = {"255.255.255.255", MCAST_GROUP, *self.extra_targets, *known}
        targets.update(bc for _, bc in _interfaces())
        for t in targets:
            try:
                self._tx.sendto(data, (t, self.port))
            except OSError:
                pass

    def _send_one(self, kind, ip):
        try:
            self._tx.sendto(self._message(kind), (ip, self.port))
        except OSError:
            pass

    def _changed(self):
        if self.on_change:
            self.on_change(self.devices())

    def _announce(self):
        while not self._stop.is_set():
            if self.visible:
                self._send("hello")
            now, gone = time.time(), False
            with self._lock:
                for k in [k for k, d in self._devices.items() if now - d["last_seen"] > TIMEOUT]:
                    del self._devices[k]
                    gone = True
            if gone:
                self._changed()
            self._stop.wait(INTERVAL)

    def _listen(self):
        while not self._stop.is_set():
            try:
                data, (ip, _) = self._rx.recvfrom(2048)
                msg = json.loads(data.decode())
            except (socket.timeout, ValueError, UnicodeDecodeError):
                continue
            except OSError:
                return
            if msg.get("app") != APP or msg.get("id") == self.id or not isinstance(msg.get("port"), int):
                continue
            changed = False
            with self._lock:
                if msg.get("t") == "bye":
                    changed = self._devices.pop(msg["id"], None) is not None
                else:
                    now = time.time()
                    prev = self._devices.get(msg["id"])
                    # Un PC relié par Wi-Fi ET câble nous écrit par deux adresses : on en garde une
                    # stable, pour que la liste ne saute pas d'une adresse à l'autre.
                    ips = {k: v for k, v in (prev["ips"] if prev else {}).items() if now - v <= TIMEOUT}
                    ips[ip] = now
                    cur = prev["ip"] if prev and prev["ip"] in ips and now - ips[prev["ip"]] <= INTERVAL * 2.5 else None
                    if cur is None:
                        cands = [k for k in ips if not k.startswith("127.")] or list(ips)
                        cur = max(cands, key=lambda k: ips[k])
                    entry = {"id": msg["id"], "name": str(msg.get("name", "?"))[:40], "ip": cur, "ips": ips,
                             "port": msg["port"], "platform": str(msg.get("platform", "pc"))[:10],
                             "last_seen": now}
                    changed = prev is None or (prev["name"], prev["ip"], prev["port"]) != (entry["name"], cur, entry["port"])
                    self._devices[msg["id"]] = entry
            if msg.get("t") == "hello" and self.visible:
                now = time.time()
                if now - self._replied.get(ip, 0) > 1.0:      # on répond directement à l'appareil
                    if len(self._replied) > 256:
                        self._replied.clear()
                    self._replied[ip] = now
                    self._send_one("reply", ip)
            if changed:
                self._changed()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Test de la détection FlashLink")
    ap.add_argument("name", nargs="?", default=socket.gethostname())
    args = ap.parse_args()
    d = Discovery(args.name, 47475, on_change=lambda ds: print(
        "\nAppareils détectés :", *[f"  - {x['name']} ({x['platform']}) {x['ip']}" for x in ds] or ["  (aucun)"], sep="\n"))
    d.start()
    print(f"« {args.name} » est visible. Lance la même commande sur l'autre appareil. Ctrl+C pour quitter.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        d.stop()
