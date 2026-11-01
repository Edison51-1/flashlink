"""FlashLink - connexion sans routeur : point d'accès Wi-Fi et câble Ethernet direct.

Les fonctions retournent (ok: bool, message: str, infos: dict | None).
ATTENTION : ces fonctions pilotent le système (nmcli / PowerShell). Elles dépendent du
matériel, des pilotes et des droits de la machine, d'où des messages de repli clairs.
"""
import platform
import secrets
import shutil
import socket
import string
import subprocess
import webbrowser

IS_WIN = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"


def _run(cmd, timeout=40):
    flags = 0x08000000 if IS_WIN else 0  # pas de fenêtre console sous Windows
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=flags)
    return r.returncode, (r.stdout + r.stderr).strip()


def local_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def _password(n=10):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


# --------------------------------------------------------------- point d'accès
_WIN_PS = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op,$type){ $t=$asTask.MakeGenericMethod($type).Invoke($null,@($op)); $t.Wait(-1)|Out-Null; $t.Result }
[void][Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]
[void][Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$p=[Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
if(-not $p){ throw 'NOPROFILE' }
$m=[Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($p)
if($m.TetheringOperationalState -ne 1){
  $r=Await ($m.StartTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
  if($r.Status -ne 0){ throw $r.AdditionalErrorMessage }
}
$c=$m.GetCurrentAccessPointConfiguration()
"$($c.Ssid)|$($c.Passphrase)"
"""


def start_hotspot():
    if IS_LINUX:
        if not shutil.which("nmcli"):
            return False, "NetworkManager (nmcli) est introuvable. Installez-le ou créez le point d'accès à la main.", None
        ssid, pw = "FlashLink-" + secrets.token_hex(2), _password()
        rc, out = _run(["nmcli", "device", "wifi", "hotspot", "ssid", ssid, "password", pw])
        if rc != 0:
            return False, f"Echec nmcli : {out}\n(Essayez avec sudo, et vérifiez que le Wi-Fi est activé.)", None
        ip = "10.42.0.1"
        return True, f"Point d'accès actif.\nWi-Fi : {ssid}\nMot de passe : {pw}\nAdresse de ce PC : {ip}", {
            "ssid": ssid, "password": pw, "ip": ip}
    if IS_WIN:
        try:
            rc, out = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _WIN_PS], 60)
        except (OSError, subprocess.SubprocessError) as e:
            rc, out = 1, str(e)
        if rc == 0 and "|" in out:
            ssid, pw = out.splitlines()[-1].split("|", 1)
            return True, f"Point d'accès actif.\nWi-Fi : {ssid}\nMot de passe : {pw}\nAdresse de ce PC : 192.168.137.1", {
                "ssid": ssid, "password": pw, "ip": "192.168.137.1"}
        try:
            webbrowser.open("ms-settings:network-mobilehotspot")
        except Exception:
            pass
        extra = ("Windows exige une connexion existante pour créer un point d'accès automatiquement. "
                 if "NOPROFILE" in out else f"({out[:200]}) ")
        return False, (extra + "J'ouvre les paramètres « Point d'accès mobile » : activez-le à la main, "
                       "puis connectez l'autre PC. Astuce : créer le point d'accès sur le PC Linux, ou "
                       "utiliser un câble Ethernet, marche sans aucune connexion."), None
    return False, "Système non pris en charge.", None


def stop_hotspot():
    if IS_LINUX and shutil.which("nmcli"):
        rc, out = _run(["nmcli", "connection", "down", "Hotspot"])
        return rc == 0, out or "Point d'accès arrêté.", None
    if IS_WIN:
        try:
            webbrowser.open("ms-settings:network-mobilehotspot")
        except Exception:
            pass
        return True, "Désactivez le point d'accès dans la fenêtre des paramètres.", None
    return False, "Rien à arrêter.", None


# --------------------------------------------------------------- câble Ethernet
def prepare_ethernet():
    if IS_LINUX:
        if not shutil.which("nmcli"):
            return False, "nmcli introuvable : configurez l'interface Ethernet en « lien local » (link-local).", None
        rc, out = _run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"])
        devs = [l.split(":")[0] for l in out.splitlines() if l.endswith(":ethernet")]
        if not devs:
            return False, "Aucune carte Ethernet détectée.", None
        dev = devs[0]
        _run(["nmcli", "connection", "delete", "flashlink-eth"])
        rc, out = _run(["nmcli", "connection", "add", "type", "ethernet", "ifname", dev, "con-name",
                        "flashlink-eth", "ipv4.method", "link-local", "ipv6.method", "ignore"])
        if rc != 0:
            return False, f"Echec nmcli : {out}", None
        rc, out = _run(["nmcli", "connection", "up", "flashlink-eth"])
        if rc != 0:
            return False, f"Câble branché ? {out}", None
        return True, f"Ethernet prêt sur {dev} (adresse automatique 169.254.x.x). Branchez l'autre PC avec le câble.", {"dev": dev}
    if IS_WIN:
        return True, ("Rien à configurer : branchez le câble RJ45 entre les deux PC. Windows attribue seul une "
                      "adresse 169.254.x.x au bout de 20 à 60 secondes. Si l'autre PC ne répond pas, autorisez "
                      "Python dans le pare-feu, y compris pour les « réseaux publics »."), None
    return False, "Système non pris en charge.", None
