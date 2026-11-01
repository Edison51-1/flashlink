#!/usr/bin/env python3
"""
FlashLink - moteur de transfert de fichiers et dossiers entre deux PC (Windows / Linux).
Etape 1 : ligne de commande. Aucune dépendance externe (Python 3.8+).

Sur le PC qui reçoit :   python flashlink.py recevoir --dossier ./Recus
Sur le PC qui envoie :   python flashlink.py envoyer MonDossier fichier.pdf --code 123456
                         (le PC récepteur est trouvé automatiquement sur le réseau local,
                          ou donnez son adresse avec --vers 192.168.1.20)
"""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

PORT = 50555          # port de transfert (TCP)
DISC_PORT = 50556     # port de découverte (UDP)
CHUNK = 1 << 20       # 1 Mo par bloc
MAGIC = "FLASHLINK/1"


# ----------------------------------------------------------------- utilitaires
def fmt_size(n):
    n = float(n)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024 or unit == "To":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_time(s):
    s = int(s)
    return f"{s // 60:d}m{s % 60:02d}s" if s >= 60 else f"{s}s"


class Progress:
    def __init__(self, total, label):
        self.total, self.done, self.label = total, 0, label
        self.t0 = time.time()
        self.last = 0

    def add(self, n):
        self.done += n
        now = time.time()
        if now - self.last >= 0.2 or self.done >= self.total:
            self.last = now
            el = max(now - self.t0, 1e-6)
            speed = self.done / el
            rest = (self.total - self.done) / speed if speed else 0
            pct = 100 * self.done / self.total if self.total else 100
            sys.stdout.write(
                f"\r{self.label}: {pct:5.1f}%  {fmt_size(self.done)}/{fmt_size(self.total)}"
                f"  {fmt_size(speed)}/s  reste {fmt_time(rest)}   "
            )
            sys.stdout.flush()

    def end(self):
        sys.stdout.write("\n")


def send_json(sock, obj):
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def read_json(rfile):
    line = rfile.readline(1 << 26)
    if not line:
        raise ConnectionError("connexion fermée")
    return json.loads(line)


def read_exact(rfile, n):
    buf = rfile.read(n)
    if buf is None or len(buf) != n:
        raise ConnectionError("connexion interrompue")
    return buf


def auth_tag(code, nonce):
    return hmac.new(code.encode(), bytes.fromhex(nonce), hashlib.sha256).hexdigest()


def safe_target(dest, rel):
    """Empêche l'écriture hors du dossier de réception."""
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or "\\" in rel or ":" in rel:
        raise ValueError(f"chemin refusé : {rel}")
    target = (dest / p).resolve()
    root = dest.resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"chemin refusé : {rel}")
    return target


# --------------------------------------------------------------- découverte
def discovery_responder(name, stop):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", DISC_PORT))
    except OSError:
        return
    s.settimeout(0.5)
    while not stop.is_set():
        try:
            data, addr = s.recvfrom(256)
        except socket.timeout:
            continue
        except OSError:
            break
        if data.strip() == b"FLASHLINK?":
            reply = json.dumps({"name": name, "port": PORT}).encode()
            s.sendto(reply, addr)
    s.close()


def discover(timeout=2.0):
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.3)
    end = time.time() + timeout
    while time.time() < end:
        try:
            s.sendto(b"FLASHLINK?", ("255.255.255.255", DISC_PORT))
        except OSError:
            pass
        try:
            while True:
                data, addr = s.recvfrom(512)
                try:
                    info = json.loads(data)
                    found[addr[0]] = info
                except ValueError:
                    pass
        except socket.timeout:
            pass
    s.close()
    return found


# ------------------------------------------------------------------ envoi
def build_manifest(paths):
    files, dirs = [], []
    for raw in paths:
        p = Path(raw).resolve()
        if not p.exists():
            raise FileNotFoundError(raw)
        if p.is_dir():
            base = p.name
            dirs.append(base)
            for root, dnames, fnames in os.walk(p):
                dnames.sort()
                rootp = Path(root)
                for d in dnames:
                    dirs.append(f"{base}/{(rootp / d).relative_to(p).as_posix()}")
                for fn in sorted(fnames):
                    fp = rootp / fn
                    rel = f"{base}/{fp.relative_to(p).as_posix()}"
                    files.append({"path": rel, "size": fp.stat().st_size, "abs": str(fp)})
        else:
            files.append({"path": p.name, "size": p.stat().st_size, "abs": str(p)})
    return files, dirs


def cmd_send(args):
    code = args.code or input("Code affiché sur le PC récepteur : ").strip()
    host = args.vers
    if not host:
        print("Recherche d'un récepteur sur le réseau local...")
        found = discover()
        if not found:
            print("Aucun récepteur trouvé. Vérifiez qu'il est lancé, ou utilisez --vers ADRESSE_IP.")
            return 1
        items = list(found.items())
        if len(items) == 1:
            host = items[0][0]
        else:
            for i, (ip, info) in enumerate(items, 1):
                print(f"  {i}. {info.get('name', '?')}  ({ip})")
            host = items[int(input("Choix : ")) - 1][0]
        print(f"Récepteur : {found[host].get('name', '?')} ({host})")

    files, dirs = build_manifest(args.chemins)
    total = sum(f["size"] for f in files)
    print(f"{len(files)} fichier(s), {fmt_size(total)} à envoyer")

    sock = socket.create_connection((host, args.port), timeout=15)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
    sock.settimeout(60)
    rfile = sock.makefile("rb", 1 << 20)
    try:
        send_json(sock, {"magic": MAGIC, "name": socket.gethostname()})
        hello = read_json(rfile)
        manifest = {"files": [{"path": f["path"], "size": f["size"]} for f in files], "dirs": dirs}
        send_json(sock, {"auth": auth_tag(code, hello["nonce"]), "manifest": manifest})
        sock.settimeout(300)  # le récepteur peut mettre du temps à accepter
        answer = read_json(rfile)
        sock.settimeout(60)
        if not answer.get("ok"):
            print("Refusé par le récepteur :", answer.get("error", "inconnu"))
            return 1
        offsets = answer.get("offsets", {})
        already = sum(offsets.get(f["path"], 0) for f in files)
        prog = Progress(total, "Envoi")
        prog.done = already
        for f in files:
            off = offsets.get(f["path"], 0)
            h = hashlib.sha256()
            with open(f["abs"], "rb") as fh:
                # reprise : on re-hache la partie déjà reçue sans la renvoyer
                left = off
                while left:
                    b = fh.read(min(CHUNK, left))
                    if not b:
                        break
                    h.update(b)
                    left -= len(b)
                while True:
                    b = fh.read(CHUNK)
                    if not b:
                        break
                    h.update(b)
                    sock.sendall(b)
                    prog.add(len(b))
            sock.sendall(h.digest())
        prog.end()
        final = read_json(rfile)
        if final.get("ok"):
            print("Transfert terminé et vérifié.")
            return 0
        print("Erreur côté récepteur :", final.get("error"))
        return 1
    except (ConnectionError, OSError) as e:
        print(f"\nTransfert interrompu ({e}). Relancez la même commande pour reprendre.")
        return 1
    finally:
        sock.close()


# --------------------------------------------------------------- réception
def handle_client(conn, addr, dest, code, auto):
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    conn.settimeout(60)
    rfile = conn.makefile("rb", 1 << 20)
    try:
        hello = read_json(rfile)
        if hello.get("magic") != MAGIC:
            return
        nonce = secrets.token_hex(16)
        send_json(conn, {"nonce": nonce})
        msg = read_json(rfile)
        if not hmac.compare_digest(msg.get("auth", ""), auth_tag(code, nonce)):
            send_json(conn, {"ok": False, "error": "code incorrect"})
            print(f"Connexion refusée depuis {addr[0]} (mauvais code).")
            return
        man = msg["manifest"]
        files, dirs = man["files"], man["dirs"]
        total = sum(f["size"] for f in files)
        sender = hello.get("name", addr[0])
        print(f"\n{sender} veut envoyer {len(files)} fichier(s), {fmt_size(total)}.")
        if not auto:
            if input("Accepter ? [o/N] ").strip().lower() not in ("o", "oui", "y"):
                send_json(conn, {"ok": False, "error": "refusé par l'utilisateur"})
                return
        dest.mkdir(parents=True, exist_ok=True)
        for d in dirs:
            safe_target(dest, d).mkdir(parents=True, exist_ok=True)
        offsets = {}
        for f in files:
            tgt = safe_target(dest, f["path"])
            part = tgt.with_name(tgt.name + ".part")
            if part.exists() and part.stat().st_size <= f["size"]:
                offsets[f["path"]] = part.stat().st_size
        send_json(conn, {"ok": True, "offsets": offsets})

        prog = Progress(total, "Réception")
        prog.done = sum(offsets.values())
        for f in files:
            tgt = safe_target(dest, f["path"])
            tgt.parent.mkdir(parents=True, exist_ok=True)
            part = tgt.with_name(tgt.name + ".part")
            off = offsets.get(f["path"], 0)
            h = hashlib.sha256()
            if off:
                with open(part, "rb") as old:
                    while True:
                        b = old.read(CHUNK)
                        if not b:
                            break
                        h.update(b)
            remaining = f["size"] - off
            with open(part, "ab" if off else "wb") as out:
                while remaining:
                    b = rfile.read(min(CHUNK, remaining))
                    if not b:
                        raise ConnectionError("connexion interrompue")
                    out.write(b)
                    h.update(b)
                    remaining -= len(b)
                    prog.add(len(b))
            digest = read_exact(rfile, 32)
            if digest != h.digest():
                part.unlink(missing_ok=True)
                send_json(conn, {"ok": False, "error": f"fichier corrompu : {f['path']}"})
                print(f"\nFichier corrompu : {f['path']}")
                return
            os.replace(part, tgt)
        prog.end()
        send_json(conn, {"ok": True})
        print(f"Reçu dans : {dest.resolve()}")
    except (ConnectionError, OSError, ValueError, KeyError) as e:
        print(f"\nTransfert interrompu ({e}). Les fichiers partiels sont gardés pour la reprise.")
    finally:
        conn.close()


def cmd_receive(args):
    dest = Path(args.dossier)
    code = args.code or f"{secrets.randbelow(10**6):06d}"
    name = socket.gethostname()
    stop = threading.Event()
    threading.Thread(target=discovery_responder, args=(name, stop), daemon=True).start()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", args.port))
    srv.listen(4)
    print(f"FlashLink prêt sur {name}. Dossier de réception : {dest.resolve()}")
    print(f"CODE DE JUMELAGE : {code}")
    print("En attente d'un envoi (Ctrl+C pour quitter)...")
    try:
        while True:
            conn, addr = srv.accept()
            handle_client(conn, addr, dest, code, args.auto)
    except KeyboardInterrupt:
        print("\nArrêt.")
    finally:
        stop.set()
        srv.close()


def main():
    ap = argparse.ArgumentParser(description="FlashLink - transfert de fichiers et dossiers entre PC")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("recevoir", help="attendre un envoi")
    r.add_argument("--dossier", default="FlashLink_Recus")
    r.add_argument("--code", help="code de jumelage (6 chiffres aléatoires par défaut)")
    r.add_argument("--port", type=int, default=PORT)
    r.add_argument("--auto", action="store_true", help="accepter sans demander")
    r.set_defaults(fn=cmd_receive)
    s = sub.add_parser("envoyer", help="envoyer fichiers/dossiers")
    s.add_argument("chemins", nargs="+")
    s.add_argument("--vers", help="adresse IP du récepteur (sinon recherche automatique)")
    s.add_argument("--code")
    s.add_argument("--port", type=int, default=PORT)
    s.set_defaults(fn=cmd_send)
    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
