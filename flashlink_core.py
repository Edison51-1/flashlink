"""FlashLink - noyau avec callbacks (utilisé par l'interface graphique)."""
import hashlib
import hmac
import os
import secrets
import select
import socket
import threading
import time
from pathlib import Path

import flashlink as fl
import flashlink_pair as pairing


class Meter:
    def __init__(self, total, cb, start=0):
        self.total, self.done, self.cb = total, start, cb
        self.t0, self.last, self.start = time.time(), 0.0, start

    def add(self, n):
        self.done += n
        now = time.time()
        if self.cb and (now - self.last >= 0.15 or self.done >= self.total):
            self.last = now
            speed = (self.done - self.start) / max(now - self.t0, 1e-6)
            self.cb(self.done, self.total, speed)


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise ConnectionError("annulé")


def send_files(host, code, paths, port=fl.PORT, on_progress=None, cancel=None, my_id=None):
    """Retourne (ok, message)."""
    try:
        files, dirs = fl.build_manifest(paths)
    except OSError as e:
        return False, f"Chemin introuvable : {e}"
    total = sum(f["size"] for f in files)
    try:
        sock = socket.create_connection((host, port), timeout=15)
    except OSError as e:
        return False, f"Impossible de joindre {host} ({e})"
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
    sock.settimeout(60)
    rfile = sock.makefile("rb", 1 << 20)
    try:
        fl.send_json(sock, {"magic": fl.MAGIC, "name": socket.gethostname(), **({"id": my_id} if my_id else {})})
        hello = fl.read_json(rfile)
        manifest = {"files": [{"path": f["path"], "size": f["size"]} for f in files], "dirs": dirs}
        fl.send_json(sock, {"auth": fl.auth_tag(code, hello["nonce"]), "manifest": manifest})
        sock.settimeout(300)  # le récepteur peut mettre du temps à accepter
        ans = fl.read_json(rfile)
        sock.settimeout(60)
        if not ans.get("ok"):
            return False, "Refusé : " + str(ans.get("error", "inconnu"))
        offsets = ans.get("offsets", {})
        meter = Meter(total, on_progress, start=sum(offsets.get(f["path"], 0) for f in files))
        for f in files:
            off = offsets.get(f["path"], 0)
            h = hashlib.sha256()
            with open(f["abs"], "rb") as fh:
                left = off
                while left:
                    b = fh.read(min(fl.CHUNK, left))
                    if not b:
                        break
                    h.update(b)
                    left -= len(b)
                while True:
                    _check(cancel)
                    b = fh.read(fl.CHUNK)
                    if not b:
                        break
                    h.update(b)
                    sock.sendall(b)
                    meter.add(len(b))
            sock.sendall(h.digest())
        final = fl.read_json(rfile)
        if final.get("ok"):
            return True, f"Transfert terminé et vérifié ({len(files)} fichier(s))."
        return False, "Erreur côté récepteur : " + str(final.get("error"))
    except (ConnectionError, OSError, ValueError) as e:
        return False, f"Transfert interrompu ({e}). Relancez pour reprendre."
    finally:
        sock.close()


def pair_with(host, my_id, my_name, port=fl.PORT, on_code=None, cancel=None, timeout=130):
    """Jumelage, côté PC qui demande. Retourne {ok, msg, id, name, key}."""
    def fail(m):
        return {"ok": False, "msg": m}
    priv, pub = pairing.new_keypair()
    try:
        sock = socket.create_connection((host, port), timeout=15)
    except OSError as e:
        return fail(f"Impossible de joindre {host} ({e})")
    sock.settimeout(30)
    rfile = sock.makefile("rb")
    try:
        fl.send_json(sock, {"magic": fl.MAGIC, "name": my_name, "mode": "pair",
                            "id": my_id, "pub": format(pub, "x"), "pv": 2})
        r1 = fl.read_json(rfile)
        if r1.get("ok") is False:
            return fail("Refusé : " + str(r1.get("error", "inconnu")))
        if "pub" not in r1:
            return fail("Cet appareil utilise une ancienne version de NexaLink : mettez-le à jour.")
        peer_pub = int(str(r1["pub"])[:600], 16)
        peer_id, peer_name = str(r1["id"])[:32], str(r1.get("name", "?"))[:40]
        k = pairing.secret(priv, peer_pub)
        if on_code:
            on_code(pairing.confirmation_code(k, pub, peer_pub), peer_name)
        deadline = time.time() + timeout
        while not select.select([sock], [], [], 0.5)[0]:   # attente de la confirmation de l'autre PC
            _check(cancel)
            if time.time() > deadline:
                return fail("Pas de réponse de l'autre PC.")
        r2 = fl.read_json(rfile)
        if not r2.get("ok"):
            return fail("Refusé : " + str(r2.get("error", "l'autre PC a refusé")))
        key = pairing.pair_key(k, pub, peer_pub)
        if not hmac.compare_digest(str(r2.get("proof", "")), pairing.proof(key)):
            return fail("Vérification échouée : jumelage annulé.")
        fl.send_json(sock, {"ack": True})   # confirme à l'autre PC que le jumelage est bien enregistré ici
        return {"ok": True, "msg": "Jumelage réussi", "id": peer_id, "name": peer_name, "key": key}
    except ConnectionError as e:
        return fail(str(e))
    except (OSError, ValueError, KeyError, TypeError) as e:
        return fail(f"Jumelage interrompu ({e})")
    finally:
        sock.close()


def unpair_with(host, port, my_id, key, timeout=8):
    """Prévient l'autre PC qu'on retire le jumelage. Retourne False si injoignable (sans erreur)."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError:
        return False
    try:
        sock.settimeout(timeout)
        rfile = sock.makefile("rb")
        fl.send_json(sock, {"magic": fl.MAGIC, "name": socket.gethostname(), "id": my_id})
        nonce = fl.read_json(rfile)["nonce"]
        fl.send_json(sock, {"auth": fl.auth_tag(key, nonce), "action": "unpair"})
        return bool(fl.read_json(rfile).get("ok"))
    except Exception:
        return False
    finally:
        sock.close()


class Receiver:
    """Serveur de réception. ask(sender, nb_fichiers, total) -> bool (peut bloquer)."""

    def __init__(self, dest, code=None, ask=None, on_progress=None, on_event=None, port=fl.PORT,
                 pair=None, get_key=None, on_paired=None, my_id="", auto_accept=None, on_unpair=None):
        self.dest = Path(dest)
        self.code = code or f"{secrets.randbelow(10**6):06d}"
        self.ask = ask or (lambda *a: True)
        self.on_progress = on_progress
        self.on_event = on_event or (lambda m: None)
        self.port = port
        self.stop_ev = threading.Event()
        self.srv = None
        self.pair, self.get_key, self.on_paired, self.my_id = pair, get_key, on_paired, my_id
        self.auto_accept, self.on_unpair = auto_accept, on_unpair
        self._pair_lock = threading.Lock()

    def start(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("", self.port))
        self.srv.listen(4)
        self.srv.settimeout(0.5)
        threading.Thread(target=fl.discovery_responder,
                         args=(socket.gethostname(), self.stop_ev), daemon=True).start()
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.stop_ev.set()
        try:
            self.srv.close()
        except OSError:
            pass

    def _loop(self):
        while not self.stop_ev.is_set():
            try:
                conn, addr = self.srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()

    def _pair(self, conn, hello, addr, rfile):
        """Jumelage, côté PC qui répond : affiche le code, attend l'accord de l'utilisateur."""
        if not self.pair or not self._pair_lock.acquire(blocking=False):
            fl.send_json(conn, {"ok": False, "error": "occupé ou indisponible"})
            return
        try:
            try:
                peer_pub = int(str(hello.get("pub", ""))[:600], 16)
                peer_id = str(hello["id"])[:32]
                if not pairing.valid_pub(peer_pub):
                    raise ValueError
            except (ValueError, KeyError):
                fl.send_json(conn, {"ok": False, "error": "demande invalide"})
                return
            priv, pub = pairing.new_keypair()
            k = pairing.secret(priv, peer_pub)
            code = pairing.confirmation_code(k, peer_pub, pub)
            name = str(hello.get("name", addr[0]))[:40]
            fl.send_json(conn, {"pub": format(pub, "x"), "id": self.my_id, "name": socket.gethostname()})
            conn.settimeout(150)
            if not self.pair(name, peer_id, code, addr[0]):
                fl.send_json(conn, {"ok": False, "error": "refusé par l'utilisateur"})
                self.on_event(f"Jumelage avec {name} refusé.")
                return
            key = pairing.pair_key(k, peer_pub, pub)
            fl.send_json(conn, {"ok": True, "proof": pairing.proof(key)})
            if hello.get("pv") == 2:      # on n'enregistre la clé qu'une fois l'autre PC sûr de l'avoir aussi
                conn.settimeout(20)
                try:
                    if not fl.read_json(rfile).get("ack"):
                        raise ValueError("pas de confirmation")
                except Exception:
                    self.on_event(f"Jumelage avec {name} non confirmé : annulé.")
                    return
            if self.on_paired:
                self.on_paired(peer_id, name, key, addr[0])
            self.on_event(f"Jumelé avec {name}.")
        finally:
            self._pair_lock.release()

    def _handle(self, conn, addr):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        conn.settimeout(60)
        rfile = conn.makefile("rb", 1 << 20)
        try:
            hello = fl.read_json(rfile)
            if hello.get("magic") != fl.MAGIC:
                return
            if hello.get("mode") == "pair":
                self._pair(conn, hello, addr, rfile)
                return
            nonce = secrets.token_hex(16)
            fl.send_json(conn, {"nonce": nonce})
            msg = fl.read_json(rfile)
            got = str(msg.get("auth", ""))
            peer_id = str(hello.get("id", ""))
            peer_key = self.get_key(peer_id) if (self.get_key and peer_id) else None
            by_key = bool(peer_key and hmac.compare_digest(got, fl.auth_tag(peer_key, nonce)))
            if not (by_key or hmac.compare_digest(got, fl.auth_tag(self.code, nonce))):
                fl.send_json(conn, {"ok": False, "error": "code incorrect"})
                why = "" if not peer_id else (" (jumelage inconnu sur ce PC)" if not peer_key else " (clé de jumelage différente)")
                self.on_event(f"Connexion refusée depuis {addr[0]} : mauvais code{why}.")
                return
            if msg.get("action") == "unpair":
                if by_key and self.on_unpair:
                    self.on_unpair(peer_id)
                    self.on_event(f"{hello.get('name', addr[0])} a retiré le jumelage.")
                fl.send_json(conn, {"ok": by_key})
                return
            files, dirs = msg["manifest"]["files"], msg["manifest"]["dirs"]
            total = sum(f["size"] for f in files)
            sender = hello.get("name", addr[0])
            if not ((by_key and self.auto_accept and self.auto_accept()) or self.ask(sender, len(files), total)):
                fl.send_json(conn, {"ok": False, "error": "refusé par l'utilisateur"})
                self.on_event(f"Envoi de {sender} refusé.")
                return
            self.on_event(f"Réception depuis {sender} : {len(files)} fichier(s), {fl.fmt_size(total)}")
            self.dest.mkdir(parents=True, exist_ok=True)
            for d in dirs:
                fl.safe_target(self.dest, d).mkdir(parents=True, exist_ok=True)
            offsets = {}
            for f in files:
                tgt = fl.safe_target(self.dest, f["path"])
                part = tgt.with_name(tgt.name + ".part")
                if part.exists() and part.stat().st_size <= f["size"]:
                    offsets[f["path"]] = part.stat().st_size
            fl.send_json(conn, {"ok": True, "offsets": offsets})
            meter = Meter(total, self.on_progress, start=sum(offsets.values()))
            for f in files:
                tgt = fl.safe_target(self.dest, f["path"])
                tgt.parent.mkdir(parents=True, exist_ok=True)
                part = tgt.with_name(tgt.name + ".part")
                off = offsets.get(f["path"], 0)
                h = hashlib.sha256()
                if off:
                    with open(part, "rb") as old:
                        while True:
                            b = old.read(fl.CHUNK)
                            if not b:
                                break
                            h.update(b)
                remaining = f["size"] - off
                with open(part, "ab" if off else "wb") as out:
                    while remaining:
                        b = rfile.read(min(fl.CHUNK, remaining))
                        if not b:
                            raise ConnectionError("connexion interrompue")
                        out.write(b)
                        h.update(b)
                        remaining -= len(b)
                        meter.add(len(b))
                if fl.read_exact(rfile, 32) != h.digest():
                    part.unlink(missing_ok=True)
                    fl.send_json(conn, {"ok": False, "error": f"fichier corrompu : {f['path']}"})
                    self.on_event(f"Fichier corrompu : {f['path']}")
                    return
                os.replace(part, tgt)
            fl.send_json(conn, {"ok": True})
            self.on_event(f"Terminé : reçu dans {self.dest.resolve()}")
        except (ConnectionError, OSError, ValueError, KeyError) as e:
            self.on_event(f"Transfert interrompu ({e}). Reprise possible.")
        finally:
            conn.close()
