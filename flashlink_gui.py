#!/usr/bin/env python3
"""FlashLink - interface graphique (Tkinter, inclus avec Python). Lancer : python flashlink_gui.py"""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

import flashlink as fl
import flashlink_core as core
import flashlink_net as net


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FlashLink - transfert de fichiers entre PC")
        self.geometry("720x620")
        self.minsize(640, 560)
        self.q = queue.Queue()
        self.paths = []
        self.receiver = None
        self.cancel = threading.Event()
        self.found = {}
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_conn, self.tab_send, self.tab_recv = ttk.Frame(nb), ttk.Frame(nb), ttk.Frame(nb)
        nb.add(self.tab_conn, text="1. Connexion")
        nb.add(self.tab_send, text="2. Envoyer")
        nb.add(self.tab_recv, text="3. Recevoir")
        self.build_conn()
        self.build_send()
        self.build_recv()
        self.after(100, self.pump)

    # ------------------------------------------------------------ thread-safe UI
    def ui(self, fn, *a):
        self.q.put((fn, a))

    def pump(self):
        try:
            while True:
                fn, a = self.q.get_nowait()
                fn(*a)
        except queue.Empty:
            pass
        self.after(100, self.pump)

    # ------------------------------------------------------------------ connexion
    def build_conn(self):
        f = self.tab_conn
        ttk.Label(f, text="Pas de Wi-Fi ni de routeur ? Créez une liaison directe entre les deux PC.",
                  wraplength=650).pack(anchor="w", padx=10, pady=(10, 4))
        row = ttk.Frame(f)
        row.pack(fill="x", padx=10, pady=4)
        ttk.Button(row, text="Créer un point d'accès Wi-Fi", command=lambda: self.run_net(net.start_hotspot)).pack(side="left")
        ttk.Button(row, text="Arrêter le point d'accès", command=lambda: self.run_net(net.stop_hotspot)).pack(side="left", padx=6)
        ttk.Button(row, text="Préparer le câble Ethernet", command=lambda: self.run_net(net.prepare_ethernet)).pack(side="left")
        self.conn_out = tk.Text(f, height=10, wrap="word")
        self.conn_out.pack(fill="both", expand=True, padx=10, pady=6)
        self.ips_var = tk.StringVar()
        ttk.Label(f, textvariable=self.ips_var, font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=10)
        ttk.Button(f, text="Afficher l'adresse de ce PC", command=self.show_ips).pack(anchor="w", padx=10, pady=4)
        tip = ("Mode d'emploi : sur un PC, créez le point d'accès (ou branchez le câble), connectez l'autre PC "
               "au Wi-Fi indiqué, puis utilisez les onglets Recevoir et Envoyer.\n"
               "Pare-feu : à la première ouverture, autorisez Python/FlashLink (réseaux privés ET publics). "
               "Ports utilisés : TCP 50555 et UDP 50556.")
        ttk.Label(f, text=tip, wraplength=650, foreground="#555").pack(anchor="w", padx=10, pady=(0, 10))
        self.show_ips()

    def show_ips(self):
        ips = net.local_ips()
        self.ips_var.set("Adresse(s) de ce PC : " + (", ".join(ips) if ips else "aucune (pas de réseau)"))

    def run_net(self, fn):
        self.conn_out.insert("end", "Patientez...\n")

        def work():
            try:
                ok, msg, _ = fn()
            except Exception as e:  # noqa
                ok, msg = False, f"Erreur : {e}"
            self.ui(self.net_done, ok, msg)
        threading.Thread(target=work, daemon=True).start()

    def net_done(self, ok, msg):
        self.conn_out.insert("end", ("OK  " if ok else "ECHEC  ") + msg + "\n\n")
        self.conn_out.see("end")
        self.show_ips()

    # --------------------------------------------------------------------- envoyer
    def build_send(self):
        f = self.tab_send
        top = ttk.Frame(f)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Button(top, text="Ajouter des fichiers", command=self.add_files).pack(side="left")
        ttk.Button(top, text="Ajouter un dossier", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(top, text="Vider", command=self.clear_paths).pack(side="left")
        self.listbox = tk.Listbox(f, height=8)
        self.listbox.pack(fill="both", expand=True, padx=10)
        grid = ttk.Frame(f)
        grid.pack(fill="x", padx=10, pady=8)
        ttk.Label(grid, text="PC destinataire :").grid(row=0, column=0, sticky="w")
        self.host_var = tk.StringVar()
        self.host_box = ttk.Combobox(grid, textvariable=self.host_var, width=28)
        self.host_box.grid(row=0, column=1, padx=6)
        ttk.Button(grid, text="Rechercher", command=self.do_discover).grid(row=0, column=2)
        ttk.Label(grid, text="Code (6 chiffres) :").grid(row=1, column=0, sticky="w", pady=6)
        self.code_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self.code_var, width=12).grid(row=1, column=1, sticky="w", padx=6)
        btns = ttk.Frame(f)
        btns.pack(fill="x", padx=10)
        self.send_btn = ttk.Button(btns, text="Envoyer", command=self.do_send)
        self.send_btn.pack(side="left")
        ttk.Button(btns, text="Annuler", command=self.cancel.set).pack(side="left", padx=6)
        self.s_bar = ttk.Progressbar(f, maximum=100)
        self.s_bar.pack(fill="x", padx=10, pady=8)
        self.s_lbl = tk.StringVar(value="Prêt.")
        ttk.Label(f, textvariable=self.s_lbl, wraplength=650).pack(anchor="w", padx=10, pady=(0, 10))

    def add_files(self):
        for p in filedialog.askopenfilenames(title="Choisir des fichiers"):
            self.add_path(p)

    def add_folder(self):
        p = filedialog.askdirectory(title="Choisir un dossier")
        if p:
            self.add_path(p)

    def add_path(self, p):
        if p not in self.paths:
            self.paths.append(p)
            self.listbox.insert("end", p)

    def clear_paths(self):
        self.paths.clear()
        self.listbox.delete(0, "end")

    def do_discover(self):
        self.s_lbl.set("Recherche des PC en réception...")

        def work():
            found = fl.discover(2.0)
            self.ui(self.discover_done, found)
        threading.Thread(target=work, daemon=True).start()

    def discover_done(self, found):
        self.found = found
        vals = [f"{ip}  ({info.get('name', '?')})" for ip, info in found.items()]
        self.host_box["values"] = vals
        if vals:
            self.host_box.current(0)
            self.s_lbl.set(f"{len(vals)} PC trouvé(s).")
        else:
            self.s_lbl.set("Aucun PC trouvé. Lancez « Recevoir » sur l'autre PC ou saisissez son adresse.")

    def do_send(self):
        host = self.host_var.get().split()[0] if self.host_var.get().strip() else ""
        code = self.code_var.get().strip()
        if not self.paths or not host or not code:
            messagebox.showwarning("FlashLink", "Choisissez des fichiers/dossiers, le PC destinataire et le code.")
            return
        self.cancel.clear()
        self.send_btn.state(["disabled"])
        self.s_lbl.set("Connexion...")

        def prog(done, total, speed):
            self.ui(self.s_progress, done, total, speed)

        def work():
            ok, msg = core.send_files(host, code, list(self.paths), on_progress=prog, cancel=self.cancel)
            self.ui(self.s_done, ok, msg)
        threading.Thread(target=work, daemon=True).start()

    def s_progress(self, done, total, speed):
        self.s_bar["value"] = 100 * done / total if total else 100
        rest = (total - done) / speed if speed else 0
        self.s_lbl.set(f"{fl.fmt_size(done)} / {fl.fmt_size(total)}  -  {fl.fmt_size(speed)}/s  -  reste {fl.fmt_time(rest)}")

    def s_done(self, ok, msg):
        self.send_btn.state(["!disabled"])
        self.s_lbl.set(msg)
        if ok:
            self.s_bar["value"] = 100

    # --------------------------------------------------------------------- recevoir
    def build_recv(self):
        f = self.tab_recv
        row = ttk.Frame(f)
        row.pack(fill="x", padx=10, pady=10)
        ttk.Label(row, text="Dossier de réception :").pack(side="left")
        self.dest_var = tk.StringVar(value=str(Path.home() / "FlashLink_Recus"))
        ttk.Entry(row, textvariable=self.dest_var, width=44).pack(side="left", padx=6)
        ttk.Button(row, text="Parcourir", command=self.pick_dest).pack(side="left")
        self.recv_btn = ttk.Button(f, text="Démarrer la réception", command=self.toggle_recv)
        self.recv_btn.pack(anchor="w", padx=10)
        self.code_big = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.code_big, font=("TkDefaultFont", 22, "bold")).pack(pady=12)
        self.r_bar = ttk.Progressbar(f, maximum=100)
        self.r_bar.pack(fill="x", padx=10)
        self.r_lbl = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.r_lbl).pack(anchor="w", padx=10, pady=4)
        self.log = tk.Text(f, height=10, wrap="word")
        self.log.pack(fill="both", expand=True, padx=10, pady=(4, 10))

    def pick_dest(self):
        p = filedialog.askdirectory(title="Dossier de réception")
        if p:
            self.dest_var.set(p)

    def toggle_recv(self):
        if self.receiver:
            self.receiver.stop()
            self.receiver = None
            self.recv_btn.config(text="Démarrer la réception")
            self.code_big.set("")
            self.log_line("Réception arrêtée.")
            return
        try:
            self.receiver = core.Receiver(
                self.dest_var.get(), ask=self.ask_user,
                on_progress=lambda d, t, s: self.ui(self.r_progress, d, t, s),
                on_event=lambda m: self.ui(self.log_line, m))
            self.receiver.start()
        except OSError as e:
            self.receiver = None
            messagebox.showerror("FlashLink", f"Impossible de démarrer ({e}). Un autre FlashLink est-il déjà ouvert ?")
            return
        self.recv_btn.config(text="Arrêter la réception")
        self.code_big.set(f"CODE : {self.receiver.code}")
        ips = net.local_ips()
        self.log_line("En attente. Donnez ce code à l'expéditeur. Adresse(s) : " + (", ".join(ips) or "?"))

    def ask_user(self, sender, nfiles, total):
        """Appelé depuis un thread réseau : on demande à l'utilisateur dans le thread de l'interface."""
        ev, res = threading.Event(), []

        def dlg():
            res.append(messagebox.askyesno("FlashLink", f"{sender} veut envoyer {nfiles} fichier(s) "
                                           f"({fl.fmt_size(total)}).\nAccepter ?"))
            ev.set()
        self.ui(dlg)
        ev.wait(300)
        return bool(res and res[0])

    def r_progress(self, done, total, speed):
        self.r_bar["value"] = 100 * done / total if total else 100
        self.r_lbl.set(f"{fl.fmt_size(done)} / {fl.fmt_size(total)}  -  {fl.fmt_size(speed)}/s")

    def log_line(self, m):
        self.log.insert("end", m + "\n")
        self.log.see("end")


if __name__ == "__main__":
    App().mainloop()
