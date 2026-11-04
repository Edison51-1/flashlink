#!/usr/bin/env python3
"""FlashLink - interface moderne (PySide6). Lancer : python flashlink_qt.py"""
import json
import os
import platform
import socket
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QStackedWidget, QTextEdit, QVBoxLayout,
                               QWidget)

import flashlink as fl
import flashlink_core as core
import flashlink_net as net
from flashlink_discovery import Discovery
from flashlink_pair import DeviceStore

BG, SIDE, CARD, CARD2 = "#0B1020", "#0E1630", "#121C3B", "#18244A"
TXT, MUTED, CYAN, GREEN, LINE = "#E6EAF5", "#9AA6C3", "#22D3EE", "#4ADE80", "#2A3866"
HIST = Path.home() / ".flashlink_history.json"
APP_VERSION = "3.10.0"


def _clean(s):
    """Nettoie un nom reçu du réseau avant de l'afficher (pas de balises ni de caractères bizarres)."""
    return "".join(ch for ch in str(s) if ch.isprintable() and ch not in "<>&")[:40] or "?"

ICONS = {
    "bolt": '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    "wifi": '<path d="M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19.5" r="1"/>',
    "up": '<path d="M12 19V5M5 12l7-7 7 7"/>',
    "down": '<path d="M12 5v14M5 12l7 7 7-7"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    "video": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
    "pc": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "devices": '<rect x="2" y="5" width="14" height="10" rx="2"/><path d="M6 19h6M9 15v4"/><rect x="18" y="8" width="4" height="9" rx="1"/>',
}
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm"}


def icon(name, color=TXT, size=20, sw=2):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')
    r = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    r.render(p)
    p.end()
    return QIcon(pm)


def icon_label(name, color, size=22, bg=CARD2, box=44):
    lab = QLabel()
    lab.setFixedSize(box, box)
    lab.setAlignment(Qt.AlignCenter)
    lab.setPixmap(icon(name, color, size).pixmap(QSize(size, size)))
    lab.setStyleSheet(f"background:{bg};border-radius:12px;")
    return lab


QSS = f"""
* {{ font-family: 'Inter','Segoe UI','Ubuntu','Noto Sans',sans-serif; color:{TXT}; font-size:14px; }}
QMainWindow, #root {{ background:{BG}; }}
#side {{ background:{SIDE}; }}
#card {{ background:{CARD}; border-radius:16px; }}
#cardActive {{ background:{CARD2}; border:1px solid {CYAN}; border-radius:14px; }}
#nav {{ background:transparent; border:0; border-radius:10px; padding:12px; text-align:left; color:{MUTED}; font-weight:500; }}
#nav:hover {{ background:{CARD}; }}
#nav:checked {{ background:{CARD2}; color:#FFFFFF; font-weight:600; }}
#primary {{ background:{CYAN}; color:#06101F; border:0; border-radius:11px; padding:12px 22px; font-weight:700; }}
#primary:hover {{ background:#67E3F5; }}
#primary:disabled {{ background:{LINE}; color:{MUTED}; }}
#ghost {{ background:transparent; border:1px solid {LINE}; border-radius:11px; padding:12px 22px; font-weight:600; }}
#ghost:hover {{ background:{CARD}; }}
#muted {{ color:{MUTED}; font-size:12px; }}
#h1 {{ font-size:26px; font-weight:700; }}
#chip {{ background:#0F2A2E; color:{GREEN}; border-radius:15px; padding:7px 14px; font-weight:600; font-size:13px; }}
#chipOff {{ background:#2A1E2E; color:#F59E9E; border-radius:15px; padding:7px 14px; font-weight:600; font-size:13px; }}
#speed {{ font-size:26px; font-weight:700; }}
#code {{ font-size:26px; font-weight:700; color:{CYAN}; letter-spacing:3px; }}
#bigcode {{ font-size:44px; font-weight:700; color:{CYAN}; letter-spacing:6px; }}
QLineEdit, QComboBox, QTextEdit, QListWidget {{ background:{CARD}; border:1px solid {LINE}; border-radius:10px; padding:9px 12px; selection-background-color:{CYAN}; selection-color:#06101F; }}
QComboBox QAbstractItemView {{ background:{CARD}; selection-background-color:{CARD2}; }}
QProgressBar {{ background:{BG}; border:0; border-radius:4px; max-height:8px; min-height:8px; }}
QProgressBar::chunk {{ background:{CYAN}; border-radius:4px; }}
#done QProgressBar::chunk {{ background:{GREEN}; }}
QScrollArea {{ border:0; background:transparent; }}
QScrollBar:vertical {{ background:transparent; width:8px; }}
QScrollBar::handle:vertical {{ background:{LINE}; border-radius:4px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; }}
"""


class Bridge(QObject):
    sprog = Signal(object, object, float)
    rprog = Signal(object, object, float)
    event = Signal(str)
    netdone = Signal(bool, str)
    found = Signal(object)
    devices = Signal(object)
    paircode = Signal(str, str)
    pairdone = Signal(object)
    pairask = Signal(str, str, str, object)
    devrefresh = Signal()
    sent = Signal(bool, str)
    ask = Signal(str, int, object, object)


class ItemCard(QFrame):
    def __init__(self, path, size, kind):
        super().__init__()
        self.setObjectName("card")
        self.size_b = size
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(14)
        ext = Path(path).suffix.lower()
        nm = "folder" if kind == "dir" else ("video" if ext in VIDEO_EXT else "file")
        self.ic = icon_label(nm, MUTED, 20, CARD2, 40)
        lay.addWidget(self.ic)
        col = QVBoxLayout()
        col.setSpacing(4)
        top = QHBoxLayout()
        self.name = QLabel(Path(path).name)
        self.name.setStyleSheet("font-weight:600;font-size:15px;")
        self.pct = QLabel("")
        self.pct.setStyleSheet(f"color:{CYAN};font-weight:600;font-size:13px;")
        top.addWidget(self.name, 1)
        top.addWidget(self.pct)
        self.sub = QLabel(f"{'dossier' if kind == 'dir' else 'fichier'} · {fl.fmt_size(size)} · en attente")
        self.sub.setObjectName("muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.hide()
        col.addLayout(top)
        col.addWidget(self.bar)
        col.addWidget(self.sub)
        lay.addLayout(col, 1)
        self.kind, self.nm = kind, nm
        self.state = "wait"

    def set_frac(self, f):
        st = "done" if f >= 0.999 else ("active" if f > 0 else "wait")
        self.bar.setValue(int(f * 1000))
        if st != self.state:
            self.state = st
            self.setObjectName("cardActive" if st == "active" else "card")
            self.bar.setVisible(st == "active")
            col = {"done": GREEN, "active": CYAN, "wait": MUTED}[st]
            self.ic.setPixmap(icon("folder" if self.kind == "dir" else self.nm, col, 20).pixmap(QSize(20, 20)))
            self.setProperty("done", st == "done")
            self.style().unpolish(self)
            self.style().polish(self)
        what = "dossier" if self.kind == "dir" else "fichier"
        if st == "done":
            self.pct.setText("Terminé ✓")
            self.pct.setStyleSheet(f"color:{GREEN};font-weight:600;font-size:13px;")
            self.sub.setText(f"{what} · {fl.fmt_size(self.size_b)}")
        elif st == "active":
            self.pct.setText(f"{int(f * 100)} %")
            self.pct.setStyleSheet(f"color:{CYAN};font-weight:600;font-size:13px;")
            self.sub.setText(f"{fl.fmt_size(self.size_b * f)} sur {fl.fmt_size(self.size_b)}")


class MainWindow(QMainWindow):
    def __init__(self, store_path=None):
        super().__init__()
        self.store = DeviceStore(store_path)
        self.pairing, self._scan = False, False
        self.pair_cancel_ev = threading.Event()
        self.setWindowTitle(f"FlashLink {APP_VERSION}")
        self.resize(1040, 680)
        self.setMinimumSize(900, 600)
        self.setAcceptDrops(True)
        self.br = Bridge()
        self.code = f"{int.from_bytes(os.urandom(3), 'big') % 10**6:06d}"
        self.paths, self.cards, self.chips = [], [], []
        self.cancel = threading.Event()
        self.receiver = None
        self.sending = False
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self.build_side())
        self.stack = QStackedWidget()
        h.addWidget(self.stack, 1)
        self.stack.addWidget(self.page_conn())
        self.stack.addWidget(self.page_dev())
        self.stack.addWidget(self.page_send())
        self.stack.addWidget(self.page_recv())
        self.stack.addWidget(self.page_hist())
        self.goto(2)
        b = self.br
        b.sprog.connect(self.on_send_progress)
        b.rprog.connect(self.on_recv_progress)
        b.event.connect(self.on_event)
        b.netdone.connect(self.on_netdone)
        b.found.connect(self.on_found)
        b.sent.connect(self.on_sent)
        b.ask.connect(self.on_ask)
        b.devices.connect(self.on_devices)
        b.paircode.connect(self.on_paircode)
        b.pairdone.connect(self.on_pairdone)
        b.pairask.connect(self.on_pairask)
        b.devrefresh.connect(self.refresh_all)
        self._auto_text = ""
        self.disc = None
        try:
            kind = "windows" if platform.system() == "Windows" else "linux"
            self.disc = Discovery(socket.gethostname(), fl.PORT, platform=kind,
                                  on_change=lambda ds: self.br.devices.emit(ds),
                                  device_id=self.store.my_id)
            self.disc.visible = False   # on n'apparaît chez les autres que pendant la réception
            self.disc.start()
        except OSError:
            self.disc = None            # port déjà pris : le bouton « Rechercher » reste disponible
        self.refresh_chip()
        self.load_hist()
        self.refresh_dev()

    # ----------------------------------------------------------------- structure
    def build_side(self):
        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(230)
        v = QVBoxLayout(side)
        v.setContentsMargins(16, 24, 16, 24)
        v.setSpacing(6)
        logo = QHBoxLayout()
        logo.setContentsMargins(8, 0, 0, 22)
        logo.addWidget(icon_label("bolt", "#06101F", 22, CYAN, 38))
        t = QLabel("FlashLink")
        t.setStyleSheet("font-size:20px;font-weight:700;")
        logo.addWidget(t, 1)
        v.addLayout(logo)
        self.navs = []
        for i, (nm, ic) in enumerate([("Connexion", "wifi"), ("Appareils", "devices"), ("Envoyer", "up"), ("Recevoir", "down"), ("Historique", "clock")]):
            b = QPushButton("  " + nm)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIcon(icon(ic, MUTED, 18))
            b.setIconSize(QSize(18, 18))
            b.clicked.connect(lambda _=False, k=i: self.goto(k))
            v.addWidget(b)
            self.navs.append((b, ic))
        v.addStretch(1)
        card = QFrame()
        card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(16, 14, 16, 14)
        cv.setSpacing(2)
        a = QLabel("Code manuel")
        a.setObjectName("muted")
        self.code_lbl = QLabel(f"{self.code[:3]} {self.code[3:]}")
        self.code_lbl.setObjectName("code")
        c = QLabel("Pour un PC non jumelé")
        c.setObjectName("muted")
        for w in (a, self.code_lbl, c):
            cv.addWidget(w)
        v.addWidget(card)
        return side

    def goto(self, k):
        self.stack.setCurrentIndex(k)
        for i, (b, ic) in enumerate(self.navs):
            b.setChecked(i == k)
            b.setIcon(icon(ic, CYAN if i == k else MUTED, 18))

    def header(self, title, sub):
        row = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(4)
        t = QLabel(title)
        t.setObjectName("h1")
        s = QLabel(sub)
        s.setObjectName("muted")
        s.setStyleSheet(f"color:{MUTED};font-size:14px;")
        col.addWidget(t)
        col.addWidget(s)
        row.addLayout(col, 1)
        chip = QLabel()
        chip.setMinimumHeight(32)
        chip.setAlignment(Qt.AlignCenter)
        row.addWidget(chip, 0, Qt.AlignTop | Qt.AlignRight)
        return row, t, s, chip

    def page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(32, 28, 32, 24)
        v.setSpacing(18)
        return w, v

    # ------------------------------------------------------------- page Appareils
    def page_dev(self):
        w, v = self.page()
        row, t, sub_, chip = self.header("Appareils", "Détectez et jumelez les PC à proximité, comme en Bluetooth")
        self.chips.append(chip)
        v.addLayout(row)
        card = QFrame()
        card.setObjectName("card")
        h = QHBoxLayout(card)
        h.setContentsMargins(24, 18, 24, 18)
        h.setSpacing(16)
        h.addWidget(icon_label("pc", CYAN, 22))
        col = QVBoxLayout()
        col.setSpacing(2)
        me = QLabel(socket.gethostname())
        me.setTextFormat(Qt.PlainText)
        me.setStyleSheet("font-size:17px;font-weight:700;")
        self.me_state = QLabel()
        self.me_state.setObjectName("muted")
        col.addWidget(me)
        col.addWidget(self.me_state)
        h.addLayout(col, 1)
        self.vis_btn = QPushButton()
        self.vis_btn.setObjectName("primary")
        self.vis_btn.clicked.connect(self.toggle_recv)
        h.addWidget(self.vis_btn)
        v.addWidget(card)

        self.pair_card = QFrame()
        self.pair_card.setObjectName("cardActive")
        pv = QVBoxLayout(self.pair_card)
        pv.setContentsMargins(24, 18, 24, 18)
        pv.setSpacing(6)
        self.pair_title = QLabel()
        self.pair_title.setTextFormat(Qt.PlainText)
        self.pair_title.setStyleSheet("font-size:16px;font-weight:700;")
        self.pair_code = QLabel()
        self.pair_code.setObjectName("bigcode")
        self.pair_msg = QLabel()
        self.pair_msg.setObjectName("muted")
        self.pair_msg.setWordWrap(True)
        self.pair_msg.setTextFormat(Qt.PlainText)
        cancel = QPushButton("Annuler")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.pair_cancel_ev.set)
        for x in (self.pair_title, self.pair_code, self.pair_msg):
            pv.addWidget(x)
        pv.addWidget(cancel, 0, Qt.AlignLeft)
        self.pair_card.hide()
        v.addWidget(self.pair_card)

        lab = QLabel("Appareils jumelés")
        lab.setStyleSheet("font-size:16px;font-weight:700;")
        v.addWidget(lab)
        self.paired_lay = QVBoxLayout()
        self.paired_lay.setSpacing(8)
        v.addLayout(self.paired_lay)

        top = QHBoxLayout()
        lab2 = QLabel("Appareils à proximité")
        lab2.setStyleSheet("font-size:16px;font-weight:700;")
        top.addWidget(lab2, 1)
        self.add_btn = QPushButton("+ Ajouter un appareil")
        self.add_btn.setObjectName("ghost")
        self.add_btn.clicked.connect(self.toggle_scan)
        top.addWidget(self.add_btn)
        v.addLayout(top)
        self.near_box = QWidget()
        nl = QVBoxLayout(self.near_box)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(8)
        self.near_msg = QLabel()
        self.near_msg.setObjectName("muted")
        self.near_msg.setWordWrap(True)
        self.near_lay = QVBoxLayout()
        self.near_lay.setSpacing(8)
        nl.addWidget(self.near_msg)
        nl.addLayout(self.near_lay)
        self.near_box.hide()
        v.addWidget(self.near_box)
        v.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.viewport().setAutoFillBackground(False)
        w.setObjectName("inner")
        w.setStyleSheet("QWidget#inner{background:transparent;}")
        sc.setWidget(w)
        return sc

    def clear_lay(self, lay):
        while lay.count():
            wd = lay.takeAt(0).widget()
            if wd:
                wd.deleteLater()

    def dev_row(self, name, sub_, buttons):
        f = QFrame()
        f.setObjectName("card")
        h = QHBoxLayout(f)
        h.setContentsMargins(18, 12, 18, 12)
        h.setSpacing(14)
        h.addWidget(icon_label("pc", CYAN, 20, CARD2, 40))
        col = QVBoxLayout()
        col.setSpacing(1)
        a = QLabel(_clean(name))
        a.setTextFormat(Qt.PlainText)
        a.setStyleSheet("font-weight:600;font-size:15px;")
        b = QLabel(sub_)
        b.setObjectName("muted")
        b.setTextFormat(Qt.PlainText)
        col.addWidget(a)
        col.addWidget(b)
        h.addLayout(col, 1)
        for label, primary, cb in buttons:
            btn = QPushButton(label)
            btn.setObjectName("primary" if primary else "ghost")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(cb)
            h.addWidget(btn)
        return f

    def refresh_all(self):
        self.on_devices(self.disc.devices() if self.disc else [])

    def refresh_dev(self):
        """Met à jour l'onglet Appareils (visibilité, appareils jumelés, appareils à proximité)."""
        on = self.receiver is not None
        self.vis_btn.setText("Devenir invisible" if on else "Devenir visible")
        self.me_state.setText("Visible : les autres PC peuvent vous trouver et se jumeler avec vous" if on
                              else "Invisible : cliquez sur « Devenir visible » pour être détecté par les autres PC")
        ds = {d["id"]: d for d in (self.disc.devices() if self.disc else [])}
        paired = self.store.paired()
        self.clear_lay(self.paired_lay)
        if not paired:
            lab = QLabel("Aucun appareil jumelé pour l'instant. Cliquez sur « Ajouter un appareil ».")
            lab.setObjectName("muted")
            self.paired_lay.addWidget(lab)
        for pid, p in paired.items():
            d = ds.get(pid)
            btns = ([("Envoyer", True, lambda _=False, i=pid: self.send_to(i))] if d else [])
            btns.append(("Oublier", False, lambda _=False, i=pid: self.forget(i)))
            self.paired_lay.addWidget(self.dev_row(p["name"], f"En ligne · {d['ip']}" if d else "Hors ligne", btns))
        self.clear_lay(self.near_lay)
        near = sorted((d for i, d in ds.items() if i not in paired), key=lambda d: d["name"].lower())
        if not self.disc:
            self.near_msg.setText("La détection automatique est indisponible (le port est déjà utilisé par un autre programme).")
        elif near:
            self.near_msg.setText("Cliquez sur « Jumeler » à côté du PC voulu.")
        else:
            self.near_msg.setText("Recherche en cours... Sur l'autre PC, ouvrez FlashLink et cliquez sur « Devenir visible ».")
        for d in near:
            self.near_lay.addWidget(self.dev_row(d["name"], d["ip"], [("Jumeler", True, lambda _=False, x=d: self.start_pair(x))]))

    def toggle_scan(self):
        self._scan = not self._scan
        self.near_box.setVisible(self._scan)
        self.add_btn.setText("Fermer" if self._scan else "+ Ajouter un appareil")
        self.refresh_dev()

    def start_pair(self, d):
        if self.pairing:
            return
        self.pairing = True
        self.pair_cancel_ev.clear()
        self._pair_ip = d["ip"]
        self.pair_title.setText(f"Jumelage avec {_clean(d['name'])}")
        self.pair_code.setText("······")
        self.pair_msg.setText("Connexion en cours...")
        self.pair_card.show()

        def work():
            res = core.pair_with(d["ip"], self.store.my_id, socket.gethostname(), port=d["port"],
                                 on_code=lambda c, n: self.br.paircode.emit(c, n), cancel=self.pair_cancel_ev)
            self.br.pairdone.emit(res)
        threading.Thread(target=work, daemon=True).start()

    def on_paircode(self, code, name):
        self.pair_code.setText(f"{code[:3]} {code[3:]}")
        self.pair_msg.setText(f"Vérifiez que ce même code s'affiche sur {_clean(name)}. Le jumelage se termine quand il accepte.")

    def on_pairdone(self, res):
        self.pairing = False
        if res.get("ok"):
            self.store.add(res["id"], _clean(res["name"]), res["key"], getattr(self, "_pair_ip", ""))
            self.pair_code.setText("✓")
            self.pair_msg.setText(f"Jumelé avec {_clean(res['name'])}. Vous pouvez maintenant lui envoyer des fichiers sans code.")
            self.on_event(f"Jumelé avec {_clean(res['name'])}.")
        else:
            self.pair_code.setText("✗")
            self.pair_msg.setText(str(res.get("msg", "Jumelage échoué.")))
        QTimer.singleShot(4000, self.pair_card.hide)
        self.refresh_all()

    def pair_blocking(self, name, peer_id, code, ip):
        h = {"ev": threading.Event(), "ok": False}
        self.br.pairask.emit(name, ip, code, h)
        h["ev"].wait(130)
        return h["ok"]

    def on_pairask(self, name, ip, code, h):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.goto(1)
        r = QMessageBox.question(self, "FlashLink", f"{_clean(name)} ({ip}) veut se jumeler avec ce PC.\n\n"
                                 f"Code de jumelage : {code[:3]} {code[3:]}\n\n"
                                 f"Vérifiez que ce même code s'affiche sur {_clean(name)}, puis cliquez sur Oui.")
        h["ok"] = r == QMessageBox.Yes
        h["ev"].set()

    def on_paired_rx(self, peer_id, name, key, ip):
        self.store.add(peer_id, _clean(name), key, ip)
        self.br.devrefresh.emit()

    def forget(self, pid):
        name = (self.store.paired().get(pid) or {}).get("name", "cet appareil")
        if QMessageBox.question(self, "FlashLink", f"Oublier {name} ? Il faudra le jumeler à nouveau pour lui envoyer des fichiers sans code.") == QMessageBox.Yes:
            self.store.forget(pid)
            self.refresh_all()

    def send_to(self, pid):
        self.goto(2)
        for i in range(self.host.count()):
            d = self.host.itemData(i)
            if isinstance(d, dict) and d.get("id") == pid:
                self.host.setCurrentIndex(i)
                break

    # --------------------------------------------------------------- page Envoyer
    def page_send(self):
        w, v = self.page()
        row, self.s_title, self.s_sub, chip1 = self.header("Envoyer", "Glissez des fichiers ou des dossiers dans la fenêtre")
        self.chips.append(chip1)
        v.addLayout(row)
        card = QFrame()
        card.setObjectName("card")
        h = QHBoxLayout(card)
        h.setContentsMargins(26, 20, 26, 20)
        h.setSpacing(20)
        me = QVBoxLayout()
        me.setSpacing(2)
        me.setAlignment(Qt.AlignVCenter)
        self.me_name = QLabel(os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "Mon PC"))
        self.me_name.setStyleSheet("font-weight:600;")
        m2 = QLabel("Ce PC")
        m2.setObjectName("muted")
        me.addWidget(self.me_name)
        me.addWidget(m2)
        h.addWidget(icon_label("pc", TXT, 26, CARD2, 52))
        h.addLayout(me)
        mid = QVBoxLayout()
        mid.setAlignment(Qt.AlignCenter)
        line = QFrame()
        line.setFixedHeight(4)
        line.setStyleSheet(f"background:{LINE};border-radius:2px;")
        self.line = line
        self.speed = QLabel("—")
        self.speed.setObjectName("speed")
        self.speed.setAlignment(Qt.AlignCenter)
        self.rest = QLabel("En attente d'un envoi")
        self.rest.setObjectName("muted")
        self.rest.setAlignment(Qt.AlignCenter)
        mid.addWidget(line)
        mid.addWidget(self.speed)
        mid.addWidget(self.rest)
        h.addLayout(mid, 1)
        you = QVBoxLayout()
        you.setSpacing(2)
        you.setAlignment(Qt.AlignVCenter)
        self.dst_name = QLabel("Destinataire")
        self.dst_name.setStyleSheet("font-weight:600;")
        self.dst_name.setAlignment(Qt.AlignRight)
        self.dst_ip = QLabel("à choisir")
        self.dst_ip.setObjectName("muted")
        self.dst_ip.setAlignment(Qt.AlignRight)
        you.addWidget(self.dst_name)
        you.addWidget(self.dst_ip)
        h.addLayout(you)
        h.addWidget(icon_label("pc", TXT, 26, CARD2, 52))
        v.addWidget(card)
        # liste
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        inner = QWidget()
        self.list_lay = QVBoxLayout(inner)
        self.list_lay.setContentsMargins(0, 0, 6, 0)
        self.list_lay.setSpacing(10)
        self.empty = QLabel("Aucun élément. Cliquez sur « Ajouter » ou déposez des fichiers ici.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(110)
        self.empty.setStyleSheet(f"color:{MUTED};border:2px dashed {LINE};border-radius:14px;")
        self.list_lay.addWidget(self.empty)
        self.list_lay.addStretch(1)
        inner.setObjectName('inner')
        inner.setStyleSheet('QWidget#inner{background:transparent;}')
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setWidget(inner)
        v.addWidget(self.scroll, 1)
        # commandes
        ctl = QHBoxLayout()
        ctl.setSpacing(10)
        for txt, fn in (("Ajouter des fichiers", self.add_files), ("Ajouter un dossier", self.add_folder)):
            b = QPushButton(txt)
            b.setObjectName("ghost")
            b.clicked.connect(fn)
            ctl.addWidget(b)
        ctl.addStretch(1)
        v.addLayout(ctl)
        tgt = QHBoxLayout()
        tgt.setSpacing(10)
        self.host = QComboBox()
        self.host.setEditable(True)
        self.host.lineEdit().setPlaceholderText("Les PC détectés apparaissent ici automatiquement")
        b = QPushButton("Rechercher")
        b.setObjectName("ghost")
        b.clicked.connect(self.do_discover)
        self.code_in = QLineEdit()
        self.code_in.setPlaceholderText("Code du récepteur")
        self.code_in.setMaxLength(6)
        self.code_in.setFixedWidth(170)
        self.send_btn = QPushButton("Envoyer")
        self.send_btn.setObjectName("primary")
        self.send_btn.clicked.connect(self.do_send)
        self.cancel_btn = QPushButton("Annuler")
        self.cancel_btn.setObjectName("ghost")
        self.cancel_btn.clicked.connect(self.cancel.set)
        tgt.addWidget(self.host, 1)
        tgt.addWidget(b)
        tgt.addWidget(self.code_in)
        v.addLayout(tgt)
        ctl.addWidget(self.send_btn)
        ctl.addWidget(self.cancel_btn)
        self.host.currentTextChanged.connect(lambda _t: self.on_sel_changed())
        return w

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.add_path(u.toLocalFile())
        self.goto(2)

    def add_files(self):
        for p in QFileDialog.getOpenFileNames(self, "Choisir des fichiers")[0]:
            self.add_path(p)

    def add_folder(self):
        p = QFileDialog.getExistingDirectory(self, "Choisir un dossier")
        if p:
            self.add_path(p)

    def add_path(self, p):
        if p in self.paths or self.sending:
            return
        try:
            files, _ = fl.build_manifest([p])
        except OSError:
            return
        size = sum(f["size"] for f in files)
        kind = "dir" if Path(p).is_dir() else "file"
        card = ItemCard(p, size, kind)
        self.paths.append(p)
        self.cards.append(card)
        self.empty.hide()
        self.list_lay.insertWidget(len(self.cards) - 1, card)
        total = sum(c.size_b for c in self.cards)
        self.s_title.setText("Prêt à envoyer")
        self.s_sub.setText(f"{len(self.cards)} élément(s) · {fl.fmt_size(total)} au total")

    def do_discover(self):
        self.rest.setText("Recherche des PC en réception...")
        threading.Thread(target=lambda: self.br.found.emit(fl.discover(2.0)), daemon=True).start()

    def on_found(self, found):
        self.host.clear()
        for ip, info in found.items():
            self.host.addItem(f"{ip}  ({info.get('name', '?')})")
        self.rest.setText(f"{len(found)} PC trouvé(s)" if found else "Aucun PC trouvé : lancez « Recevoir » sur l'autre PC")
        if found:
            self.update_dest()

    def sel(self):
        """PC choisi pour l'envoi : {'ip','port','name','id','key'} (id=None si adresse saisie à la main)."""
        t = self.host.currentText().strip()
        if not t:
            return None
        i = self.host.currentIndex()
        if i >= 0 and self.host.itemText(i) == t and isinstance(self.host.itemData(i), dict):
            return self.host.itemData(i)
        return {"ip": t.split()[0], "port": fl.PORT, "name": "PC destinataire", "id": None, "key": None}

    def on_devices(self, ds):
        """Liste des PC visibles, mise à jour toute seule (comme le Bluetooth)."""
        paired = self.store.paired()
        cur = self.sel()
        cur_id = cur.get("id") if cur else None
        manual = self.host.currentText().strip() if (cur and cur.get("id") is None) else ""
        self.host.blockSignals(True)
        self.host.clear()
        for d in ds:
            key = (paired.get(d["id"]) or {}).get("key")
            label = f"{d['ip']}  ({_clean(d['name'])})  " + ("✓ jumelé" if key else "· code requis")
            self.host.addItem(label, {"ip": d["ip"], "port": d["port"], "name": _clean(d["name"]),
                                      "id": d["id"], "key": key})
        ids = [d["id"] for d in ds]
        if cur_id in ids:
            self.host.setCurrentIndex(ids.index(cur_id))
        elif manual:
            self.host.setEditText(manual)         # adresse saisie à la main : on la garde
        elif ds:
            self.host.setCurrentIndex(0)
        else:
            self.host.setEditText("")
        self.host.blockSignals(False)
        self.rest.setText(f"{len(ds)} PC visible(s)" if ds else "Aucun PC visible : il doit cliquer sur « Devenir visible »")
        self.on_sel_changed()
        self.refresh_dev()

    def on_sel_changed(self):
        s = self.sel()
        paired = bool(s and s.get("key"))
        self.code_in.setEnabled(not paired)
        self.code_in.setPlaceholderText("Appareil jumelé : aucun code" if paired else "Code du récepteur")
        self.update_dest()

    def update_dest(self):
        s = self.sel()
        if s:
            self.dst_ip.setText(s["ip"])
            self.dst_name.setText(s["name"])

    def do_send(self):
        s = self.sel()
        code = (s or {}).get("key") or self.code_in.text().strip()
        if not self.paths or not s or not code:
            QMessageBox.information(self, "FlashLink", "Ajoutez des éléments, choisissez le PC destinataire (jumelé, ou saisissez son code).")
            return
        self.update_dest()
        host, port = s["ip"], s.get("port", fl.PORT)
        self.cancel.clear()
        self.sending = True
        self.send_btn.setEnabled(False)
        self.s_title.setText("Envoi en cours")
        self.rest.setText("Connexion...")
        self.t0 = time.time()
        paths = list(self.paths)

        def work():
            ok, msg = core.send_files(host, code, paths, port=port, my_id=self.store.my_id, on_progress=lambda d, t, s: self.br.sprog.emit(d, t, s),
                                      cancel=self.cancel)
            self.br.sent.emit(ok, msg)
        threading.Thread(target=work, daemon=True).start()

    def on_send_progress(self, done, total, speed):
        self.speed.setText(f"{fl.fmt_size(speed)}/s")
        rest = (total - done) / speed if speed else 0
        self.rest.setText(f"reste environ {fl.fmt_time(rest)}")
        self.line.setStyleSheet(f"background:{CYAN};border-radius:2px;")
        cum = 0
        for c in self.cards:
            f = 1.0 if c.size_b == 0 and done >= cum else (min(max((done - cum) / c.size_b, 0), 1) if c.size_b else 0)
            c.set_frac(f)
            cum += c.size_b

    def on_sent(self, ok, msg):
        self.sending = False
        self.send_btn.setEnabled(True)
        self.rest.setText(msg)
        self.line.setStyleSheet(f"background:{LINE};border-radius:2px;")
        if ok:
            for c in self.cards:
                c.set_frac(1.0)
            self.s_title.setText("Envoi terminé")
            self.speed.setText("✓")
        else:
            self.s_title.setText("Envoi interrompu")
        total = sum(c.size_b for c in self.cards)
        self.add_hist("Envoi", f"{len(self.cards)} élément(s), {fl.fmt_size(total)}", ok)

    # -------------------------------------------------------------- page Recevoir
    def page_recv(self):
        w, v = self.page()
        row, t, s, chip = self.header("Recevoir", "Démarrez la réception puis donnez le code à l'expéditeur")
        self.chips.append(chip)
        v.addLayout(row)
        card = QFrame()
        card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(26, 22, 26, 22)
        a = QLabel("Code de jumelage")
        a.setObjectName("muted")
        self.big = QLabel(f"{self.code[:3]} {self.code[3:]}")
        self.big.setObjectName("bigcode")
        self.addr = QLabel("")
        self.addr.setObjectName("muted")
        cv.addWidget(a)
        cv.addWidget(self.big)
        cv.addWidget(self.addr)
        v.addWidget(card)
        d = QHBoxLayout()
        self.dest = QLineEdit(str(Path.home() / "FlashLink_Recus"))
        pick = QPushButton("Parcourir")
        pick.setObjectName("ghost")
        pick.clicked.connect(self.pick_dest)
        d.addWidget(self.dest, 1)
        d.addWidget(pick)
        v.addLayout(d)
        self.recv_btn = QPushButton("Démarrer la réception")
        self.recv_btn.setObjectName("primary")
        self.recv_btn.clicked.connect(self.toggle_recv)
        v.addWidget(self.recv_btn, 0, Qt.AlignLeft)
        self.r_bar = QProgressBar()
        self.r_bar.setRange(0, 1000)
        self.r_bar.setTextVisible(False)
        self.r_lbl = QLabel("")
        self.r_lbl.setObjectName("muted")
        v.addWidget(self.r_bar)
        v.addWidget(self.r_lbl)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)
        return w

    def pick_dest(self):
        p = QFileDialog.getExistingDirectory(self, "Dossier de réception")
        if p:
            self.dest.setText(p)

    def toggle_recv(self):
        if self.receiver:
            self.receiver.stop()
            self.receiver = None
            if self.disc:
                self.disc.set_visible(False)
            self.recv_btn.setText("Démarrer la réception")
            self.on_event("Réception arrêtée.")
            self.refresh_dev()
            return
        try:
            self.receiver = core.Receiver(
                self.dest.text(), code=self.code,
                ask=self.ask_blocking,
                on_progress=lambda d, t, s: self.br.rprog.emit(d, t, s),
                on_event=self.br.event.emit,
                pair=self.pair_blocking, get_key=self.store.get_key,
                on_paired=self.on_paired_rx, my_id=self.store.my_id)
            self.receiver.start()
        except OSError as e:
            self.receiver = None
            QMessageBox.warning(self, "FlashLink", f"Impossible de démarrer ({e}).\nFlashLink est-il déjà ouvert ?")
            return
        self.recv_btn.setText("Arrêter la réception")
        if self.disc:
            self.disc.set_visible(True)
        ips = net.local_ips()
        self.addr.setText("Adresse de ce PC : " + (", ".join(ips) if ips else "aucune (pas de réseau)"))
        self.on_event("En attente d'un envoi...")
        self.refresh_dev()

    def ask_blocking(self, sender, n, total):
        h = {"ev": threading.Event(), "ok": False}
        self.br.ask.emit(sender, n, total, h)
        h["ev"].wait(300)
        return h["ok"]

    def on_ask(self, sender, n, total, h):
        r = QMessageBox.question(self, "FlashLink", f"{_clean(sender)} veut envoyer {n} fichier(s) ({fl.fmt_size(total)}).\nAccepter ?")
        h["ok"] = r == QMessageBox.Yes
        h["ev"].set()

    def on_recv_progress(self, done, total, speed):
        self.r_bar.setValue(int(1000 * done / total) if total else 1000)
        self.r_lbl.setText(f"{fl.fmt_size(done)} / {fl.fmt_size(total)}  ·  {fl.fmt_size(speed)}/s")

    def on_event(self, m):
        self.log.append(m)
        if m.startswith("Terminé"):
            self.add_hist("Réception", m, True)
        elif "interrompu" in m or "corrompu" in m:
            self.add_hist("Réception", m, False)

    # ------------------------------------------------------------ page Connexion
    def page_conn(self):
        w, v = self.page()
        row, t, s, chip = self.header("Connexion", "Pas de routeur ? Créez une liaison directe entre les deux PC")
        self.chips.append(chip)
        v.addLayout(row)
        g = QHBoxLayout()
        g.setSpacing(12)
        for txt, fn, prim in (("Créer un point d'accès Wi-Fi", net.start_hotspot, True),
                              ("Préparer le câble Ethernet", net.prepare_ethernet, False),
                              ("Arrêter le point d'accès", net.stop_hotspot, False)):
            b = QPushButton(txt)
            b.setObjectName("primary" if prim else "ghost")
            b.clicked.connect(lambda _=False, f=fn: self.run_net(f))
            g.addWidget(b)
        g.addStretch(1)
        v.addLayout(g)
        self.ips = QLabel()
        self.ips.setStyleSheet("font-weight:600;")
        v.addWidget(self.ips)
        self.net_out = QTextEdit()
        self.net_out.setReadOnly(True)
        v.addWidget(self.net_out, 1)
        tip = QLabel("Le plus fiable sans routeur : le PC Linux crée le point d'accès et le PC Windows s'y connecte, "
                     "ou bien un câble Ethernet direct. Autorisez FlashLink dans le pare-feu (réseaux privés et publics) ; "
                     "ports : TCP 50555 et UDP 50556.")
        tip.setWordWrap(True)
        tip.setObjectName("muted")
        v.addWidget(tip)
        return w

    def run_net(self, fn):
        self.net_out.append("Patientez...")

        def work():
            try:
                ok, msg, _ = fn()
            except Exception as e:  # noqa
                ok, msg = False, f"Erreur : {e}"
            self.br.netdone.emit(ok, msg)
        threading.Thread(target=work, daemon=True).start()

    def on_netdone(self, ok, msg):
        self.net_out.append(("OK  " if ok else "ECHEC  ") + msg + "\n")
        self.refresh_chip()

    def refresh_chip(self):
        ips = net.local_ips()
        self.ips.setText("Adresse(s) de ce PC : " + (", ".join(ips) if ips else "aucune"))
        for c in self.chips:
            if ips:
                c.setObjectName("chip")
                c.setText(f"●  Réseau local · {ips[0]}")
            else:
                c.setObjectName("chipOff")
                c.setText("●  Pas de réseau")
            c.style().unpolish(c)
            c.style().polish(c)

    # ----------------------------------------------------------- page Historique
    def page_hist(self):
        w, v = self.page()
        row, t, s, chip = self.header("Historique", "Vos derniers transferts")
        self.chips.append(chip)
        v.addLayout(row)
        self.hist = QListWidget()
        v.addWidget(self.hist, 1)
        return w

    def load_hist(self):
        try:
            for e in json.loads(HIST.read_text("utf-8"))[-100:]:
                self.hist.insertItem(0, f"{'✓' if e['ok'] else '✗'}  {e['t']}  ·  {e['dir']}  ·  {e['txt']}")
        except (OSError, ValueError, KeyError):
            pass

    def add_hist(self, d, txt, ok):
        e = {"t": time.strftime("%d/%m/%Y %H:%M"), "dir": d, "txt": txt, "ok": ok}
        self.hist.insertItem(0, f"{'✓' if ok else '✗'}  {e['t']}  ·  {d}  ·  {txt}")
        try:
            old = json.loads(HIST.read_text("utf-8")) if HIST.exists() else []
            HIST.write_text(json.dumps((old + [e])[-100:], ensure_ascii=False), "utf-8")
        except (OSError, ValueError):
            pass

    def closeEvent(self, e):
        if self.receiver:
            self.receiver.stop()
        if self.disc:
            self.disc.stop()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
