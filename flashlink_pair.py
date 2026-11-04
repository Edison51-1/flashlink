"""FlashLink - jumelage entre PC (comme le Bluetooth).

Le jumelage échange une clé secrète entre deux PC (Diffie-Hellman 2048 bits) et affiche le même
code à 6 chiffres sur les deux écrans : l'utilisateur vérifie qu'ils sont identiques, puis accepte.
Ensuite, les deux PC se reconnaissent sans code. Ce module contient aussi la liste des appareils jumelés.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

# Groupe Diffie-Hellman n°14 (RFC 3526, 2048 bits), g = 2.
P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74"
    "020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F1437"
    "4FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF05"
    "98DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB"
    "9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718"
    "3995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF",
    16)
G = 2


def new_keypair():
    priv = secrets.randbits(256) | (1 << 255)
    return priv, pow(G, priv, P)


def valid_pub(x):
    return isinstance(x, int) and 2 <= x <= P - 2


def _b(x):
    return x.to_bytes(256, "big")


def secret(priv, peer_pub):
    if not valid_pub(peer_pub):
        raise ValueError("clé invalide")
    return pow(peer_pub, priv, P)


def confirmation_code(k, pub_asker, pub_answerer):
    """Code à 6 chiffres affiché sur les deux PC (demandeur d'abord, puis répondeur)."""
    h = hashlib.sha256(b"flashlink-code" + _b(pub_asker) + _b(pub_answerer) + _b(k)).digest()
    return f"{int.from_bytes(h[:4], 'big') % 10**6:06d}"


def pair_key(k, pub_asker, pub_answerer):
    return hmac.new(_b(k), b"flashlink-key" + _b(pub_asker) + _b(pub_answerer), hashlib.sha256).hexdigest()


def proof(key):
    return hmac.new(key.encode(), b"flashlink-accepted", hashlib.sha256).hexdigest()


class DeviceStore:
    """Identité de ce PC et liste des appareils jumelés (fichier dans le dossier personnel)."""

    def __init__(self, path=None):
        self.path = Path(path) if path else Path.home() / ".flashlink_devices.json"
        self._lock = threading.Lock()
        self._d = {"id": "", "paired": {}}
        try:
            self._d.update(json.loads(self.path.read_text("utf-8")))
        except (OSError, ValueError):
            pass
        if not isinstance(self._d.get("paired"), dict):
            self._d["paired"] = {}
        if not self._d.get("id"):
            self._d["id"] = secrets.token_hex(6)
            self._save()

    @property
    def my_id(self):
        return self._d["id"]

    def paired(self):
        with self._lock:
            return {k: dict(v) for k, v in self._d["paired"].items()}

    def get_key(self, peer_id):
        with self._lock:
            return (self._d["paired"].get(peer_id) or {}).get("key")

    def add(self, peer_id, name, key, ip=""):
        with self._lock:
            self._d["paired"][peer_id] = {"name": name, "key": key, "ip": ip, "date": time.strftime("%Y-%m-%d")}
            self._save()

    def forget(self, peer_id):
        with self._lock:
            self._d["paired"].pop(peer_id, None)
            self._save()

    def _save(self):
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self._d, indent=1), "utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        except OSError:
            pass
