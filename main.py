import sys
import datetime
import urllib.parse
import sqlite3
import shutil
import os
import time
import hashlib
import secrets
import logging
from logging.handlers import RotatingFileHandler
import webbrowser
import csv
import tkcalendar
from fpdf import FPDF
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import tkinter as tk
from contextlib import contextmanager
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
from pathlib import Path
import platformdirs


# ============================================================
#  UTILIDADES
# ============================================================
def resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, relative_path)


def _safe_int(value, default=3):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def solo_digitos_entry(entry):
    def _filter(event):
        if event.keysym in ("BackSpace", "Delete", "Left", "Right",
                            "Home", "End", "Tab", "Up", "Down"):
            return
        current = entry.get()
        filtered = "".join(c for c in current if c.isdigit())
        if filtered != current:
            entry.delete(0, "end")
            entry.insert(0, filtered)
    entry.bind("<KeyRelease>", _filter)


def validar_cedula(ced):
    return ced.isdigit() and 6 <= len(ced) <= 9


def validar_nombre(nom):
    if len(nom.strip()) < 3:
        return False
    return all(c.isalpha() or c.isspace() for c in nom)


def validar_telefono(tel):
    digits = "".join(c for c in tel if c.isdigit())
    if not (10 <= len(digits) <= 11):
        return False
    return digits.startswith("04") or digits.startswith("4")


def parse_fecha(s):
    try:
        return datetime.datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

COLORS = {
    "bg_main": "#0A0A0A",
    "sidebar": "#121212",
    "card": "#1A1A1A",
    "card_hover": "#262626",
    "accent_primary": "#FFE600",
    "accent_hover": "#D4BE00",
    "accent_success": "#10B981",
    "accent_danger": "#EF4444",
    "accent_warning": "#F59E0B",
    "text_main": "#FFFFFF",
    "text_dark": "#000000",
    "text_muted": "#A1A1AA",
    "border": "#27272A",
}



# ============================================================
#  SEGURIDAD — HASH DE CONTRASEÑAS (PBKDF2 + salt)
# ============================================================
PBKDF2_ITERATIONS = 260_000

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"

def verify_password(password: str, stored: str) -> bool:
    if not stored: return False
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iters, salt_hex, hash_hex = stored.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
            return secrets.compare_digest(dk.hex(), hash_hex)
        except Exception: return False
    legacy = hashlib.sha256(password.encode()).hexdigest()
    return secrets.compare_digest(legacy, stored)

def is_legacy_hash(stored: str) -> bool:
    return bool(stored) and not stored.startswith("pbkdf2_sha256$")

# ============================================================
#  BASE DE DATOS
# ============================================================

class Database:
    def __init__(self):
        self.db_dir = Path(platformdirs.user_data_dir("MarvinGym", "MarvinGym"))
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db_name = str(self.db_dir / "gym.db")
        self.init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    _TABLAS_PERMITIDAS = {"socios", "pagos", "asistencias", "medidas", "configuracion"}
    def _columnas(self, tabla):
        if tabla not in self._TABLAS_PERMITIDAS: raise ValueError(f"Tabla no permitida: {tabla}")
        with self.get_connection() as conn:
            return [r[1] for r in conn.execute(f"PRAGMA table_info({tabla})").fetchall()]

    def init_db(self):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS socios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cedula TEXT UNIQUE,
                    nombre TEXT NOT NULL,
                    telefono TEXT,
                    plan TEXT,
                    monto REAL,
                    metodo_pago TEXT,
                    referencia TEXT,
                    fecha_ingreso TEXT,
                    fecha_vencimiento TEXT,
                    activo INTEGER DEFAULT 1
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pagos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    socio_id INTEGER,
                    monto REAL,
                    metodo_pago TEXT,
                    referencia TEXT,
                    concepto TEXT,
                    fecha TEXT,
                    FOREIGN KEY(socio_id) REFERENCES socios(id) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS asistencias (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    socio_id INTEGER,
                    fecha_hora TEXT,
                    FOREIGN KEY(socio_id) REFERENCES socios(id) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS medidas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    socio_id INTEGER,
                    fecha TEXT,
                    peso REAL,
                    altura REAL,
                    notas TEXT,
                    FOREIGN KEY(socio_id) REFERENCES socios(id) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS configuracion (
                    clave TEXT PRIMARY KEY,
                    valor TEXT
                )
            """)

            cols_socios = self._columnas("socios")
            for col, tipo in (("cedula", "TEXT"), ("referencia", "TEXT")):
                if col not in cols_socios:
                    cur.execute(f"ALTER TABLE socios ADD COLUMN {col} {tipo}")
            if "referencia" not in self._columnas("pagos"):
                cur.execute("ALTER TABLE pagos ADD COLUMN referencia TEXT")

            for idx in (
                "CREATE INDEX IF NOT EXISTS idx_socios_venc ON socios(fecha_vencimiento)",
                "CREATE INDEX IF NOT EXISTS idx_pagos_socio ON pagos(socio_id)",
                "CREATE INDEX IF NOT EXISTS idx_asist_socio ON asistencias(socio_id)",
                "CREATE INDEX IF NOT EXISTS idx_medidas_socio ON medidas(socio_id)",
            ):
                cur.execute(idx)

            pass_default = hash_password("admin123")
            defaults = [
                ("dias_alerta", "3"),
                ("wa_plantilla", "Hola {nombre}, te recordamos que tu membresía de Marvin Gym ({plan}) vence el {vencimiento}. ¡Te esperamos!"),
                ("admin_password_hash", pass_default),
                ("must_change_password", "1"),
            ]
            for clave, val in defaults:
                cur.execute("INSERT OR IGNORE INTO configuracion (clave, valor) VALUES (?, ?)", (clave, val))

    def ejecutar(self, query, params=(), fetch=False):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            return cur.fetchall() if fetch else cur.lastrowid

    def get_config(self):
        res = self.ejecutar("SELECT clave, valor FROM configuracion", fetch=True)
        return {r[0]: r[1] for r in res}

    def set_config(self, clave, valor):
        self.ejecutar("INSERT OR REPLACE INTO configuracion (clave, valor) VALUES (?, ?)", (clave, str(valor)))

    def verificar_admin_pass(self, password_plana):
        stored = self.get_config().get("admin_password_hash", "")
        if not verify_password(password_plana, stored): return False
        if is_legacy_hash(stored):
            self.set_config("admin_password_hash", hash_password(password_plana))
            logging.info("Hash de admin migrado.")
        return True

    def cambiar_admin_pass(self, nueva_password_plana):
        self.set_config("admin_password_hash", hash_password(nueva_password_plana))
        self.set_config("must_change_password", "0")

    def necesita_cambio_password(self) -> bool:
        return self.get_config().get("must_change_password", "1") == "1"


# ============================================================
#  WHATSAPP
# ============================================================
class WhatsAppManager:
    @staticmethod
    def _normalizar_numero(num):
        digits = "".join(c for c in str(num) if c.isdigit())
        if not digits:
            return ""
        if len(digits) == 11 and digits.startswith("0"):
            return "58" + digits[1:]
        if len(digits) == 10:
            return "58" + digits
        return digits

    @staticmethod
    def enviar_mensaje(num, mensaje):
        num_intl = WhatsAppManager._normalizar_numero(num)
        if not num_intl:
            return False, "Número de teléfono inválido."
        webbrowser.open(f"https://wa.me/{num_intl}?text={urllib.parse.quote(mensaje)}")
        return True, "Abriendo WhatsApp Web..."


# ============================================================
#  APP PRINCIPAL
# ============================================================
class GymApp(ctk.CTk):

    def _on_search_change(self, _event=None):
        if getattr(self, "_search_job", None) is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(250, lambda: self.cargar_tabla(self.search_entry.get()))

    def _forzar_cambio_password(self):
        dialog = ctk.CTkInputDialog(text="Por seguridad debes establecer una nueva contraseña de administrador.\n\nMínimo 6 caracteres.", title="Cambio Obligatorio")
        nueva = dialog.get_input()
        if not nueva or len(nueva) < 6:
            messagebox.showerror("Error", "La contraseña debe tener al menos 6 caracteres. La aplicación se cerrará.")
            self.after(100, self.destroy)
            return
        self.db.cambiar_admin_pass(nueva)
        messagebox.showinfo("Listo", "Contraseña actualizada. La anterior ya NO funciona.")

    PRECIOS = {"Plan Mensual": 10.0, "Plan Semanal": 3.0, "Plan Diario": 1.0}
    METODOS = ["Pago Móvil", "Efectivo", "Punto de Venta", "Transferencia", "Zelle"]

    def __init__(self):
        super().__init__()

        self.title("MARVIN GYM - Control & Automatización")
        self.geometry("1200x720")
        self.configure(fg_color=COLORS["bg_main"])
        self._cargar_icono()

        self.db = Database()
        self.selected_socio_id = None

        # Super-usuario
        self._super_unlocked = False
        self._brand_clicks = 0
        self._brand_last_click = 0.0

        self.crear_interfaz()
        self.configurar_estilos_tabla()
        self.cargar_tabla()
        self.actualizar_kpis()

        try:
            self.bind("<Control-Shift-KeyPress-S>", lambda e: self._unlock_super())
            self.bind("<Control-Shift-KeyPress-s>", lambda e: self._unlock_super())
        except Exception:
            pass

        if self.db.necesita_cambio_password():
            self.after(600, self._forzar_cambio_password)

    def _cargar_icono(self):
        for name in ("assets/icon.ico", "assets/icon.png"):
            path = resource_path(name)
            if not os.path.exists(path):
                continue
            try:
                if path.lower().endswith(".ico"):
                    self.iconbitmap(path)
                else:
                    self.iconphoto(True, tk.PhotoImage(file=path))
                return
            except Exception:
                continue

    # ------------------------------------------------------------------
    #  UI
    # ------------------------------------------------------------------
    def crear_interfaz(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=240, fg_color=COLORS["sidebar"], corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        self.lbl_brand = ctk.CTkLabel(
            self.sidebar, text="⚡ MARVIN GYM",
            font=("Inter", 22, "bold"), text_color=COLORS["accent_primary"])
        self.lbl_brand.pack(pady=(25, 20), padx=20, anchor="w")
        self.lbl_brand.bind("<Button-1>", self._on_brand_click)

        btn = {"height": 36, "corner_radius": 8, "font": ("Inter", 12, "bold"), "anchor": "w"}

        ctk.CTkLabel(self.sidebar, text="OPERACIÓN", font=("Inter", 10, "bold"),
                     text_color=COLORS["text_muted"]).pack(pady=(4, 4), padx=20, anchor="w")

        ctk.CTkButton(self.sidebar, text="  ⚡ ACCESO", command=self.ventana_acceso,
                      fg_color=COLORS["accent_primary"], text_color=COLORS["text_dark"],
                      hover_color=COLORS["accent_hover"], **btn).pack(pady=3, padx=15, fill="x")
        ctk.CTkButton(self.sidebar, text="  + Registrar Socio", command=self.ventana_nuevo_socio,
                      fg_color="transparent", hover_color=COLORS["card_hover"],
                      text_color=COLORS["text_main"], **btn).pack(pady=3, padx=15, fill="x")
        ctk.CTkButton(self.sidebar, text="  👤 Ver / Editar Socio", command=self._abrir_socio,
                      fg_color="transparent", hover_color=COLORS["card_hover"],
                      text_color=COLORS["text_main"], **btn).pack(pady=3, padx=15, fill="x")
        ctk.CTkButton(self.sidebar, text="  ↻ Renovar Membresía", command=self.ventana_renovar,
                      fg_color=COLORS["accent_success"], hover_color="#059669",
                      **btn).pack(pady=3, padx=15, fill="x")
        ctk.CTkButton(self.sidebar, text="  💬 Aviso WhatsApp", command=self.enviar_whatsapp,
                      fg_color=COLORS["accent_warning"], text_color=COLORS["text_dark"],
                      hover_color="#D97706", **btn).pack(pady=3, padx=15, fill="x")

        ctk.CTkLabel(self.sidebar, text="ADMINISTRACIÓN 🔒", font=("Inter", 10, "bold"),
                     text_color=COLORS["text_muted"]).pack(pady=(20, 4), padx=20, anchor="w")

        ctk.CTkButton(self.sidebar, text="  📊 Reportes y Caja", command=self.ventana_reportes,
                      fg_color="transparent", hover_color=COLORS["card_hover"],
                      text_color=COLORS["text_main"], **btn).pack(pady=3, padx=15, fill="x")
        ctk.CTkButton(self.sidebar, text="  🕒 Asistencias", command=self.ventana_asistencias,
                      fg_color="transparent", hover_color=COLORS["card_hover"],
                      text_color=COLORS["text_main"], **btn).pack(pady=3, padx=15, fill="x")
        ctk.CTkButton(self.sidebar, text="  ⚙ Ajustes",
                      command=lambda: self.solicitar_clave_admin(self.ventana_config),
                      fg_color="transparent", hover_color=COLORS["card_hover"],
                      text_color=COLORS["text_main"], **btn).pack(pady=3, padx=15, fill="x")
        ctk.CTkButton(self.sidebar, text="  🗑 Eliminar Socio", command=self.eliminar_socio,
                      fg_color="transparent", hover_color=COLORS["card_hover"],
                      text_color=COLORS["accent_danger"], **btn).pack(pady=3, padx=15, fill="x")

        # ---------- MAIN ----------
        self.main = ctk.CTkFrame(self, fg_color="transparent")
        self.main.grid(row=0, column=1, sticky="nsew", padx=25, pady=20)
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self.main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 15))
        ctk.CTkLabel(header, text="Panel General de Atletas", font=("Inter", 22, "bold"),
                     text_color=COLORS["text_main"]).pack(side="left")

        # Checkbox para ver inactivos
        self.show_inactive_var = tk.BooleanVar(value=False)
        chk_inactive = ctk.CTkCheckBox(
            header, text="Mostrar inactivos", variable=self.show_inactive_var,
            command=lambda: self.cargar_tabla(self.search_entry.get()),
            fg_color=COLORS["accent_primary"], text_color=COLORS["text_muted"],
            hover_color=COLORS["accent_hover"], font=("Inter", 11)
        )
        chk_inactive.pack(side="right", padx=(0, 15))

        self.search_entry = ctk.CTkEntry(header, placeholder_text="🔍 Buscar por nombre o C.I...",
                                         width=260, height=36, corner_radius=20,
                                         fg_color=COLORS["sidebar"], border_color=COLORS["border"],
                                         text_color=COLORS["text_main"])
        self.search_entry.pack(side="right", padx=(0, 10))
        self.search_entry.bind("<KeyRelease>", self._on_search_change)

        kpi = ctk.CTkFrame(self.main, fg_color="transparent")
        kpi.grid(row=1, column=0, sticky="ew", pady=(0, 15))
        kpi.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="kpi")

        self.card_activos = self.crear_kpi_card(kpi, "SOCIOS ACTIVOS", "0", COLORS["accent_primary"], 0)
        self.card_por_vencer = self.crear_kpi_card(kpi, "POR VENCER", "0", COLORS["accent_warning"], 1)
        self.card_vencidos = self.crear_kpi_card(kpi, "VENCIDOS", "0", COLORS["accent_danger"], 2)
        self.card_ingresos = self.crear_kpi_card(kpi, "CAJA DEL MES", "$0.00", COLORS["accent_success"], 3)

        self.table_card = ctk.CTkFrame(self.main, fg_color=COLORS["card"], corner_radius=12)
        self.table_card.grid(row=2, column=0, sticky="nsew")
        self.table_card.grid_columnconfigure(0, weight=1)
        self.table_card.grid_rowconfigure(0, weight=1)

        columns = ("ID", "Cédula", "Nombre", "Teléfono", "Plan", "Monto", "Pago",
                   "Ref / PagoMóvil", "Ingreso", "Vencimiento", "Estado")
        self.tree = ttk.Treeview(self.table_card, columns=columns, show="headings", selectmode="browse")
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, anchor="center", width=85)
        self.tree.column("Nombre", width=140, anchor="w")
        self.tree.column("Cédula", width=90)
        self.tree.column("Ref / PagoMóvil", width=110)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        sb = ttk.Scrollbar(self.table_card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=sb.set)
        sb.grid(row=0, column=1, sticky="ns", pady=10, padx=(0, 10))

        self.tree.bind("<<TreeviewSelect>>", self.on_select_socio)
        self.tree.bind("<Double-1>", lambda e: self._abrir_socio())
        self.tree.bind("<Button-3>", self.mostrar_menu_contextual)
        self.tree.bind("<Button-2>", self.mostrar_menu_contextual)

        self.context_menu = tk.Menu(self, tearoff=0, bg=COLORS["sidebar"], fg=COLORS["text_main"],
                                    activebackground=COLORS["accent_primary"],
                                    activeforeground=COLORS["text_dark"], bd=1)
        self.context_menu.add_command(label="👤 Ver / Editar Socio", command=self._abrir_socio)
        self.context_menu.add_command(label="↻ Renovar Membresía", command=self.ventana_renovar)
        self.context_menu.add_command(label="💬 Enviar WhatsApp", command=self.enviar_whatsapp)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="🗑 Eliminar Socio", command=self.eliminar_socio)

    def crear_kpi_card(self, parent, titulo, valor_inicial, color, col):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card"], corner_radius=10,
                            border_width=1, border_color=COLORS["border"])
        card.grid(row=0, column=col, sticky="ew", padx=4)
        ctk.CTkFrame(card, width=4, fg_color=color, corner_radius=2).pack(
            side="left", fill="y", padx=(8, 4), pady=10)
        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(side="left", fill="both", expand=True, padx=6, pady=10)
        ctk.CTkLabel(content, text=titulo, font=("Inter", 10, "bold"),
                     text_color=COLORS["text_muted"], anchor="w").pack(fill="x")
        lbl = ctk.CTkLabel(content, text=valor_inicial, font=("Inter", 18, "bold"),
                           text_color=COLORS["text_main"], anchor="w")
        lbl.pack(fill="x")
        return lbl

    def configurar_estilos_tabla(self):
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background=COLORS["card"], foreground=COLORS["text_main"],
                        rowheight=30, fieldbackground=COLORS["card"], bordercolor=COLORS["card"],
                        font=("Inter", 10))
        style.map("Treeview", background=[("selected", COLORS["accent_hover"])],
                  foreground=[("selected", COLORS["text_dark"])])
        style.configure("Treeview.Heading", background=COLORS["sidebar"], foreground=COLORS["text_muted"],
                        relief="flat", font=("Inter", 10, "bold"))
        self.tree.tag_configure("vencido", foreground=COLORS["accent_danger"])
        self.tree.tag_configure("por_vencer", foreground=COLORS["accent_warning"])
        self.tree.tag_configure("activo", foreground=COLORS["accent_success"])
        self.tree.tag_configure("inactivo", foreground=COLORS["text_muted"])

    # ------------------------------------------------------------------
    #  SUPER USUARIO (Respaldos)
    # ------------------------------------------------------------------
    def _on_brand_click(self, event=None):
        now = time.time()
        if now - self._brand_last_click > 2.0:
            self._brand_clicks = 1
        else:
            self._brand_clicks += 1
        self._brand_last_click = now
        if self._brand_clicks >= 5:
            self._brand_clicks = 0
            self._unlock_super()

    def _unlock_super(self):
        if self._super_unlocked:
            return
        dialog = ctk.CTkInputDialog(text="Contraseña de Super Usuario:", title="Acceso Restringido")
        clave = dialog.get_input()
        if clave is None:
            return
        if not self.db.verificar_admin_pass(clave):
            messagebox.showerror("Error", "Contraseña incorrecta.")
            return
        self._super_unlocked = True
        ctk.CTkButton(
            self.sidebar, text="  💾 Respaldos", command=self.ventana_respaldos,
            fg_color="transparent", hover_color=COLORS["card_hover"],
            text_color=COLORS["accent_primary"], height=36, corner_radius=8,
            font=("Inter", 12, "bold"), anchor="w"
        ).pack(pady=3, padx=15, fill="x")
        messagebox.showinfo("Super Usuario", "Modo super usuario activado.")

    # ------------------------------------------------------------------
    #  CARGA DE DATOS
    # ------------------------------------------------------------------
    def cargar_tabla(self, filtro=""):
        for row in self.tree.get_children():
            self.tree.delete(row)

        if self.show_inactive_var.get():
            sql = ("SELECT id, cedula, nombre, telefono, plan, monto, metodo_pago, referencia, "
                   "fecha_ingreso, fecha_vencimiento, activo FROM socios WHERE 1=1")
        else:
            sql = ("SELECT id, cedula, nombre, telefono, plan, monto, metodo_pago, referencia, "
                   "fecha_ingreso, fecha_vencimiento, activo FROM socios WHERE activo=1")

        params = ()
        if filtro:
            sql += " AND (nombre LIKE ? OR cedula LIKE ?)"
            params = (f"%{filtro}%", f"%{filtro}%")
        sql += " ORDER BY id DESC"

        dias_alerta = _safe_int(self.db.get_config().get("dias_alerta", "3"), 3)
        hoy = datetime.date.today()

        for s in self.db.ejecutar(sql, params, fetch=True):
            sid, ced, nombre, tel, plan, monto, m_pago, ref, f_ing, f_venc, activo = s
            
            if activo == 0:
                estado, tag = "🚫 Inactivo", "inactivo"
            else:
                try:
                    dias = (datetime.datetime.strptime(f_venc, "%Y-%m-%d").date() - hoy).days
                except (ValueError, TypeError):
                    dias = -999
                
                if dias < 0:
                    estado, tag = "✖ Vencido", "vencido"
                elif dias <= dias_alerta:
                    estado, tag = f"⚠ Vence ({dias}d)", "por_vencer"
                else:
                    estado, tag = "✔ Activo", "activo"
                    
            self.tree.insert("", "end", values=(
                sid, ced or "-", nombre, tel or "-", plan or "-", f"${(monto or 0):.2f}",
                m_pago or "Efectivo", ref or "-", f_ing or "-", f_venc or "-", estado
            ), tags=(tag,))

    def on_select_socio(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        if values:
            try:
                self.selected_socio_id = int(values[0])
            except (ValueError, IndexError):
                self.selected_socio_id = None

    def actualizar_kpis(self):
        dias = _safe_int(self.db.get_config().get("dias_alerta", "3"), 3)
        activos = self.db.ejecutar("SELECT COUNT(*) FROM socios WHERE activo=1", fetch=True)[0][0]
        por_vencer = self.db.ejecutar(
            "SELECT COUNT(*) FROM socios WHERE activo=1 AND fecha_vencimiento BETWEEN date('now') AND date('now', '+' || ? || ' days')",
            (dias,), fetch=True)[0][0]
        vencidos = self.db.ejecutar(
            "SELECT COUNT(*) FROM socios WHERE activo=1 AND fecha_vencimiento < date('now')",
            fetch=True)[0][0]
        inicio_mes = datetime.date.today().replace(day=1).strftime("%Y-%m-%d")
        total = self.db.ejecutar("SELECT COALESCE(SUM(monto),0) FROM pagos WHERE date(fecha) >= date(?)",
                                 (inicio_mes,), fetch=True)[0][0]
        self.card_activos.configure(text=str(activos))
        self.card_por_vencer.configure(text=str(por_vencer))
        self.card_vencidos.configure(text=str(vencidos))
        self.card_ingresos.configure(text=f"${total:.2f}")

    # ------------------------------------------------------------------
    #  AUXILIARES
    # ------------------------------------------------------------------
    def solicitar_clave_admin(self, callback_exito):
        dialog = ctk.CTkInputDialog(text="Ingrese la clave de Administrador:", title="Acceso Restringido")
        clave = dialog.get_input()
        if clave is None:
            return
        if self.db.verificar_admin_pass(clave):
            callback_exito()
        else:
            messagebox.showerror("Acceso Denegado", "La contraseña de administrador es incorrecta.")

    def _abrir_socio(self):
        if not self.selected_socio_id:
            messagebox.showwarning("Atención", "Selecciona un socio en la tabla primero.")
            return
        self.ventana_socio_detalle(self.selected_socio_id)

    def mostrar_menu_contextual(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.tree.selection_set(item)
        self.on_select_socio()
        if self.selected_socio_id:
            self.context_menu.post(event.x_root, event.y_root)

    def _precio_plan(self, plan):
        return self.PRECIOS.get(plan, 0.0)

    def _fecha_vencimiento(self, plan, base=None):
        base = base or datetime.date.today()
        if plan == "Plan Mensual":
            return base + datetime.timedelta(days=30)
        if plan == "Plan Semanal":
            return base + datetime.timedelta(days=7)
        return base + datetime.timedelta(days=1)

    # ------------------------------------------------------------------
    #  REPORTES Y CAJA
    # ------------------------------------------------------------------
    def ventana_reportes(self):
        top = ctk.CTkToplevel(self)
        top.title("Reportes y Caja")
        top.geometry("820x600")
        top.configure(fg_color=COLORS["bg_main"])
        top.transient(self)

        ctk.CTkLabel(top, text="📊 Reporte de Ingresos y Actividad",
                     font=("Inter", 18, "bold"), text_color=COLORS["text_main"]).pack(pady=(15, 10))

        tabview = ctk.CTkTabview(top, fg_color=COLORS["card"])
        tabview.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        tab_diario = tabview.add("Hoy")
        tab_semanal = tabview.add("Esta Semana")
        tab_mensual = tabview.add("Este Mes")

        def cargar_datos(parent, fecha_inicio):
            pagos = self.db.ejecutar("SELECT COALESCE(SUM(monto),0), COUNT(*) FROM pagos WHERE date(fecha) >= ?", (fecha_inicio,), fetch=True)[0]
            total_caja, num_pagos = pagos
            nuevos = self.db.ejecutar("SELECT COUNT(*) FROM socios WHERE date(fecha_ingreso) >= ?", (fecha_inicio,), fetch=True)[0][0]
            asistencias = self.db.ejecutar("SELECT COUNT(*) FROM asistencias WHERE date(fecha_hora) >= ?", (fecha_inicio,), fetch=True)[0][0]

            kpi_frame = ctk.CTkFrame(parent, fg_color="transparent")
            kpi_frame.pack(fill="x", pady=10, padx=10)
            
            def crear_mini_kpi(parent, titulo, valor, color):
                frame = ctk.CTkFrame(parent, fg_color=COLORS["sidebar"], corner_radius=8)
                frame.pack(side="left", expand=True, fill="both", padx=5)
                ctk.CTkLabel(frame, text=titulo, font=("Inter", 10, "bold"), text_color=COLORS["text_muted"]).pack(pady=(8,0))
                ctk.CTkLabel(frame, text=str(valor), font=("Inter", 20, "bold"), text_color=color).pack(pady=(0,8))

            crear_mini_kpi(kpi_frame, "CAJA TOTAL", f"${total_caja:.2f}", COLORS["accent_success"])
            crear_mini_kpi(kpi_frame, "NUEVOS SOCIOS", nuevos, COLORS["accent_primary"])
            crear_mini_kpi(kpi_frame, "ASISTENCIAS", asistencias, COLORS["text_main"])
            crear_mini_kpi(kpi_frame, "N° PAGOS", num_pagos, COLORS["text_main"])

            frame_tabla = ctk.CTkFrame(parent, fg_color=COLORS["sidebar"])
            frame_tabla.pack(fill="both", expand=True, padx=10, pady=(10, 10))

            cols = ("Fecha", "Concepto", "Método", "Monto")
            tree = ttk.Treeview(frame_tabla, columns=cols, show="headings")
            for c in cols:
                tree.heading(c, text=c)
                tree.column(c, anchor="center", width=120)
            tree.column("Monto", width=80)
            tree.pack(fill="both", expand=True, padx=5, pady=5)

            registros = self.db.ejecutar("""
                SELECT fecha, concepto, metodo_pago, monto 
                FROM pagos WHERE date(fecha) >= ? ORDER BY id DESC
            """, (fecha_inicio,), fetch=True)

            for r in registros:
                tree.insert("", "end", values=(r[0][:16], r[1], r[2], f"${r[3]:.2f}"))

            def exportar():
                if not registros:
                    messagebox.showwarning("Aviso", "No hay datos para exportar.", parent=top)
                    return
                archivo = filedialog.asksaveasfilename(
                    defaultextension=".csv",
                    filetypes=[("CSV", "*.csv")],
                    initialfile=f"reporte_{fecha_inicio}.csv",
                    parent=top
                )
                if not archivo:
                    return
                try:
                    with open(archivo, "w", newline="", encoding="utf-8") as f:
                        writer = csv.writer(f, delimiter=';')
                        writer.writerow(["Fecha", "Concepto", "Método", "Monto"])
                        for r in registros:
                            writer.writerow([r[0], r[1], r[2], r[3]])
                    messagebox.showinfo("Éxito", f"Reporte exportado a:\n{archivo}", parent=top)
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo exportar: {e}", parent=top)

            ctk.CTkButton(parent, text="📥 Exportar a Excel (CSV)", command=exportar,
                          fg_color="transparent", border_width=1, border_color=COLORS["text_muted"],
                          text_color=COLORS["text_main"], width=200).pack(pady=(0, 15))

        hoy = datetime.date.today()
        inicio_semana = hoy - datetime.timedelta(days=hoy.weekday())
        inicio_mes = hoy.replace(day=1)

        cargar_datos(tab_diario, hoy.strftime("%Y-%m-%d"))
        cargar_datos(tab_semanal, inicio_semana.strftime("%Y-%m-%d"))
        cargar_datos(tab_mensual, inicio_mes.strftime("%Y-%m-%d"))

    # ------------------------------------------------------------------
    #  ACCESO
    # ------------------------------------------------------------------
    def ventana_acceso(self):
        top = ctk.CTkToplevel(self)
        top.title("CONTROL DE ACCESO")
        top.geometry("550x450")
        top.configure(fg_color=COLORS["bg_main"])
        top.transient(self)

        ctk.CTkLabel(top, text="⚡ CONTROL DE ACCESO POR CÉDULA",
                     font=("Inter", 18, "bold"), text_color=COLORS["accent_primary"]).pack(pady=(20, 10))

        ent = ctk.CTkEntry(top, placeholder_text="Ingrese o escanee Cédula y presione Enter...",
                           font=("Inter", 14), width=320, height=40,
                           fg_color=COLORS["card"], border_color=COLORS["border"])
        ent.pack(pady=10)
        ent.focus()
        solo_digitos_entry(ent)

        frame = ctk.CTkFrame(top, fg_color=COLORS["card"], corner_radius=12,
                             border_width=1, border_color=COLORS["border"])
        frame.pack(fill="both", expand=True, padx=25, pady=15)

        lbl_status = ctk.CTkLabel(frame, text="ESPERANDO CÉDULA...",
                                  font=("Inter", 20, "bold"), text_color=COLORS["text_muted"])
        lbl_status.pack(pady=(25, 5))
        lbl_info = ctk.CTkLabel(frame, text="", font=("Inter", 15))
        lbl_info.pack(pady=5)
        lbl_venc = ctk.CTkLabel(frame, text="", font=("Inter", 13), text_color=COLORS["text_muted"])
        lbl_venc.pack(pady=5)

        def consultar(_event=None):
            ced = ent.get().strip()
            if not ced:
                return
            res = self.db.ejecutar(
                "SELECT id, nombre, plan, fecha_vencimiento, activo FROM socios WHERE cedula=?",
                (ced,), fetch=True)
            hoy = datetime.date.today().strftime("%Y-%m-%d")

            if res:
                sid, nombre, plan, f_venc, activo = res[0]
                if f_venc and f_venc >= hoy and activo == 1:
                    frame.configure(fg_color="#103820", border_color=COLORS["accent_success"])
                    lbl_status.configure(text="✅ ACCESO PERMITIDO", text_color=COLORS["accent_success"])
                    lbl_info.configure(text=f"Atleta: {nombre}\nPlan: {plan}")
                    lbl_venc.configure(text=f"Vencimiento: {f_venc}")
                    self.db.ejecutar(
                        "INSERT INTO asistencias (socio_id, fecha_hora) VALUES (?, ?)",
                        (sid, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                else:
                    frame.configure(fg_color="#381010", border_color=COLORS["accent_danger"])
                    lbl_status.configure(text="❌ MEMBRESÍA VENCIDA O INACTIVA", text_color=COLORS["accent_danger"])
                    lbl_info.configure(text=f"Atleta: {nombre}\nPlan: {plan}")
                    lbl_venc.configure(text=f"Venció el: {f_venc}")
            else:
                frame.configure(fg_color=COLORS["card"], border_color=COLORS["accent_warning"])
                lbl_status.configure(text="⚠️ NO REGISTRADO", text_color=COLORS["accent_warning"])
                lbl_info.configure(text=f"La cédula {ced} no existe en el sistema.")
                lbl_venc.configure(text="Regístrelo con '+ Registrar Socio'")

            ent.delete(0, "end")
            ent.focus()

        ent.bind("<Return>", consultar)
        ctk.CTkButton(top, text="Verificar Acceso [Enter]", command=consultar,
                      fg_color=COLORS["accent_primary"], text_color=COLORS["text_dark"],
                      hover_color=COLORS["accent_hover"]).pack(pady=(0, 20))

    # ------------------------------------------------------------------
    #  NUEVO SOCIO
    # ------------------------------------------------------------------
    def ventana_nuevo_socio(self):
        top = ctk.CTkToplevel(self)
        top.title("Registrar Nuevo Socio")
        top.geometry("520x750")
        top.configure(fg_color=COLORS["bg_main"])
        top.transient(self)

        ctk.CTkLabel(top, text="Nuevo Registro de Atleta",
                     font=("Inter", 16, "bold"), text_color=COLORS["text_main"]).pack(pady=(15, 10))

        ctk.CTkLabel(top, text="Cédula *", font=("Inter", 11, "bold"),
                     text_color=COLORS["text_muted"]).pack(anchor="w", padx=30)
        ent_ced = ctk.CTkEntry(top, placeholder_text="Solo números (6–9 dígitos)",
                               fg_color=COLORS["card"], border_color=COLORS["border"])
        ent_ced.pack(fill="x", padx=30, pady=(0, 6))
        solo_digitos_entry(ent_ced)

        ctk.CTkLabel(top, text="Nombre completo *", font=("Inter", 11, "bold"),
                     text_color=COLORS["text_muted"]).pack(anchor="w", padx=30)
        ent_nombre = ctk.CTkEntry(top, placeholder_text="Ej: Juan Pérez",
                                  fg_color=COLORS["card"], border_color=COLORS["border"])
        ent_nombre.pack(fill="x", padx=30, pady=(0, 6))

        ctk.CTkLabel(top, text="Teléfono WhatsApp *", font=("Inter", 11, "bold"),
                     text_color=COLORS["text_muted"]).pack(anchor="w", padx=30)
        ent_tel = ctk.CTkEntry(top, placeholder_text="Ej: 04121234567",
                               fg_color=COLORS["card"], border_color=COLORS["border"])
        ent_tel.pack(fill="x", padx=30, pady=(0, 6))
        solo_digitos_entry(ent_tel)

        ctk.CTkLabel(top, text="Plan *", font=("Inter", 11, "bold"),
                     text_color=COLORS["text_muted"]).pack(anchor="w", padx=30)
        ent_monto = ctk.CTkEntry(top, fg_color=COLORS["card"], border_color=COLORS["border"])

        def al_cambiar_plan(choice):
            ent_monto.delete(0, "end")
            ent_monto.insert(0, str(self._precio_plan(choice)))

        cb_plan = ctk.CTkComboBox(top, values=list(self.PRECIOS.keys()), command=al_cambiar_plan,
                                  state="readonly",
                                  fg_color=COLORS["card"], border_color=COLORS["border"],
                                  button_color=COLORS["accent_primary"],
                                  button_hover_color=COLORS["accent_hover"])
        cb_plan.pack(fill="x", padx=30, pady=(0, 6))
        cb_plan.set("Plan Mensual")

        ctk.CTkLabel(top, text="Monto ($) *", font=("Inter", 11, "bold"),
                     text_color=COLORS["text_muted"]).pack(anchor="w", padx=30)
        ent_monto.pack(fill="x", padx=30, pady=(0, 6))
        ent_monto.insert(0, str(self.PRECIOS["Plan Mensual"]))

        ctk.CTkLabel(top, text="Método de pago *", font=("Inter", 11, "bold"),
                     text_color=COLORS["text_muted"]).pack(anchor="w", padx=30)
        cb_pago = ctk.CTkComboBox(top, values=self.METODOS, state="readonly",
                                  fg_color=COLORS["card"], border_color=COLORS["border"],
                                  button_color=COLORS["accent_primary"],
                                  button_hover_color=COLORS["accent_hover"])
        cb_pago.pack(fill="x", padx=30, pady=(0, 6))
        cb_pago.set("Pago Móvil")

        ctk.CTkLabel(top, text="Referencia / Últimos 4 dígitos *",
                     font=("Inter", 11, "bold"), text_color=COLORS["text_muted"]).pack(anchor="w", padx=30)
        ent_ref = ctk.CTkEntry(top, placeholder_text="Ej: 1234",
                               fg_color=COLORS["card"], border_color=COLORS["border"])
        ent_ref.pack(fill="x", padx=30, pady=(0, 6))

        def on_metodo_change(choice):
            if choice == "Efectivo" and not ent_ref.get().strip():
                ent_ref.insert(0, "-")

        cb_pago.configure(command=on_metodo_change)

        def guardar():
            ced = ent_ced.get().strip()
            nom = ent_nombre.get().strip()
            tel = ent_tel.get().strip()
            plan = cb_plan.get()
            m_pago = cb_pago.get()
            ref = ent_ref.get().strip()

            if not ced:
                messagebox.showerror("Error", "La cédula es obligatoria.", parent=top); ent_ced.focus(); return
            if not validar_cedula(ced):
                messagebox.showerror("Error", "La cédula debe tener entre 6 y 9 dígitos numéricos.", parent=top)
                ent_ced.focus(); return
            if not nom:
                messagebox.showerror("Error", "El nombre es obligatorio.", parent=top); ent_nombre.focus(); return
            if not validar_nombre(nom):
                messagebox.showerror("Error", "El nombre debe tener al menos 3 letras y no contener números ni símbolos.", parent=top)
                ent_nombre.focus(); return
            if not tel:
                messagebox.showerror("Error", "El teléfono es obligatorio.", parent=top); ent_tel.focus(); return
            if not validar_telefono(tel):
                messagebox.showerror("Error", "El teléfono debe tener 10 u 11 dígitos y empezar con 04 o 4.", parent=top)
                ent_tel.focus(); return
            if plan not in self.PRECIOS:
                messagebox.showerror("Error", "Selecciona un plan válido.", parent=top); return
            try:
                monto = float(ent_monto.get().strip().replace(",", "."))
                if monto <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Error", "El monto debe ser un número mayor a cero.", parent=top)
                ent_monto.focus(); return
            if m_pago not in self.METODOS:
                messagebox.showerror("Error", "Selecciona un método de pago válido.", parent=top); return
            if not ref:
                messagebox.showerror("Error", "La referencia es obligatoria.", parent=top); ent_ref.focus(); return
            if self.db.ejecutar("SELECT 1 FROM socios WHERE cedula=?", (ced,), fetch=True):
                messagebox.showerror("Error", f"La cédula {ced} ya está registrada.", parent=top)
                ent_ced.focus(); return

            f_ingreso = datetime.date.today()
            f_venc = self._fecha_vencimiento(plan, f_ingreso)
            f_actual = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            try:
                with self.db.get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO socios (cedula, nombre, telefono, plan, monto, metodo_pago, "
                        "referencia, fecha_ingreso, fecha_vencimiento) VALUES (?,?,?,?,?,?,?,?,?)",
                        (ced, nom, tel, plan, monto, m_pago, ref,
                         f_ingreso.strftime("%Y-%m-%d"), f_venc.strftime("%Y-%m-%d")))
                    socio_id = cur.lastrowid
                    cur.execute(
                        "INSERT INTO pagos (socio_id, monto, metodo_pago, referencia, concepto, fecha) "
                        "VALUES (?,?,?,?,?,?)",
                        (socio_id, monto, m_pago, ref, f"Inscripción - {plan}", f_actual))
            except sqlite3.IntegrityError:
                messagebox.showerror("Error", "Esa cédula ya está registrada con otro socio.", parent=top)
                return

            messagebox.showinfo("Éxito", f"Socio {nom} registrado correctamente.", parent=top)
            top.destroy()
            self.cargar_tabla(self.search_entry.get())
            self.actualizar_kpis()

        ctk.CTkButton(top, text="Guardar Socio", command=guardar,
                      fg_color=COLORS["accent_success"], hover_color="#059669").pack(pady=20)

    # ------------------------------------------------------------------
    #  RENOVAR
    # ------------------------------------------------------------------
    def ventana_renovar(self):
        if not self.selected_socio_id:
            messagebox.showwarning("Atención", "Selecciona un socio en la tabla primero.")
            return

        actual = self.db.ejecutar("SELECT plan, monto FROM socios WHERE id=?",
                                  (self.selected_socio_id,), fetch=True)
        if not actual:
            messagebox.showerror("Error", "Socio no encontrado.")
            return
        plan_actual, monto_actual = actual[0]

        top = ctk.CTkToplevel(self)
        top.title("Renovar Membresía")
        top.geometry("440x440")
        top.configure(fg_color=COLORS["bg_main"])
        top.transient(self)

        ctk.CTkLabel(top, text="Renovar Membresía", font=("Inter", 16, "bold"),
                     text_color=COLORS["text_main"]).pack(pady=(15, 10))

        ent_monto = ctk.CTkEntry(top, fg_color=COLORS["card"], border_color=COLORS["border"])

        def cambiar_plan(choice):
            ent_monto.delete(0, "end")
            ent_monto.insert(0, str(self._precio_plan(choice)))

        cb_plan = ctk.CTkComboBox(top, values=list(self.PRECIOS.keys()), command=cambiar_plan,
                                  state="readonly",
                                  fg_color=COLORS["card"], border_color=COLORS["border"],
                                  button_color=COLORS["accent_primary"],
                                  button_hover_color=COLORS["accent_hover"])
        cb_plan.pack(fill="x", padx=30, pady=6)
        cb_plan.set(plan_actual or "Plan Mensual")

        ent_monto.pack(fill="x", padx=30, pady=6)
        ent_monto.insert(0, str(monto_actual if monto_actual is not None
                                else self._precio_plan(cb_plan.get())))

        cb_pago = ctk.CTkComboBox(top, values=self.METODOS, state="readonly",
                                  fg_color=COLORS["card"], border_color=COLORS["border"],
                                  button_color=COLORS["accent_primary"],
                                  button_hover_color=COLORS["accent_hover"])
        cb_pago.pack(fill="x", padx=30, pady=6)
        cb_pago.set("Pago Móvil")

        ent_ref = ctk.CTkEntry(top, placeholder_text="Referencia / Últimos 4 dígitos",
                               fg_color=COLORS["card"], border_color=COLORS["border"])
        ent_ref.pack(fill="x", padx=30, pady=6)

        def confirmar():
            plan = cb_plan.get()
            m_pago = cb_pago.get()
            ref = ent_ref.get().strip()

            if plan not in self.PRECIOS:
                messagebox.showerror("Error", "Selecciona un plan válido.", parent=top); return
            try:
                monto = float(ent_monto.get().strip().replace(",", "."))
                if monto <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Error", "Monto inválido.", parent=top); return
            if m_pago not in self.METODOS:
                messagebox.showerror("Error", "Selecciona un método de pago.", parent=top); return
            if not ref:
                messagebox.showerror("Error", "La referencia es obligatoria.", parent=top); return

            hoy = datetime.date.today()
            f_venc = self._fecha_vencimiento(plan, hoy)
            f_actual = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            try:
                with self.db.get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "UPDATE socios SET plan=?, monto=?, metodo_pago=?, referencia=?, "
                        "fecha_vencimiento=?, activo=1 WHERE id=?",
                        (plan, monto, m_pago, ref, f_venc.strftime("%Y-%m-%d"),
                         self.selected_socio_id))
                    cur.execute(
                        "INSERT INTO pagos (socio_id, monto, metodo_pago, referencia, concepto, fecha) "
                        "VALUES (?,?,?,?,?,?)",
                        (self.selected_socio_id, monto, m_pago, ref,
                         f"Renovación - {plan}", f_actual))
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo renovar:\n{e}", parent=top); return

            messagebox.showinfo("Éxito", "Membresía renovada.", parent=top)
            top.destroy()
            self.cargar_tabla(self.search_entry.get())
            self.actualizar_kpis()

        ctk.CTkButton(top, text="Confirmar Renovación", command=confirmar,
                      fg_color=COLORS["accent_success"], hover_color="#059669").pack(pady=20)

    # ------------------------------------------------------------------
    #  DETALLE DEL SOCIO (Pestañas)
    # ------------------------------------------------------------------
    def ventana_socio_detalle(self, socio_id, tab_activa="Datos"):
        data = self.db.ejecutar(
            "SELECT cedula, nombre, telefono, plan, monto, metodo_pago, referencia, "
            "fecha_ingreso, fecha_vencimiento FROM socios WHERE id=?",
            (socio_id,), fetch=True)
        if not data:
            messagebox.showerror("Error", "Socio no encontrado.")
            return
        ced, nom, tel, plan, monto, met, ref, f_ing, f_venc = data[0]

        top = ctk.CTkToplevel(self)
        top.title(f"Socio: {nom}")
        top.geometry("1100x800")
        top.configure(fg_color=COLORS["bg_main"])
        top.transient(self)

        head = ctk.CTkFrame(top, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(15, 5))
        ctk.CTkLabel(head, text=f"👤 {nom}", font=("Inter", 20, "bold"),
                     text_color=COLORS["accent_primary"]).pack(side="left")
        ctk.CTkLabel(head, text=f"C.I: {ced or 'N/A'}  |  Vence: {f_venc or 'N/A'}",
                     font=("Inter", 12), text_color=COLORS["text_muted"]).pack(side="right")

        tabview = ctk.CTkTabview(top, fg_color=COLORS["card"], corner_radius=10)
        tabview.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        tab_datos = tabview.add("Datos")
        tab_prog = tabview.add("Progreso")
        tab_pagos = tabview.add("Pagos")
        tab_asist = tabview.add("Asistencias")

        def recargar(tab="Datos"):
            top.destroy()
            self.ventana_socio_detalle(socio_id, tab_activa=tab)

        self._build_tab_datos(tab_datos, socio_id, ced, nom, tel, plan, monto, met, ref,
                              f_ing, f_venc, recargar)
        self._build_tab_progreso(tab_prog, socio_id, recargar)
        self._build_tab_pagos(tab_pagos, socio_id)
        self._build_tab_asist(tab_asist, socio_id)

        try:
            tabview.set(tab_activa)
        except Exception:
            pass

    def _build_tab_datos(self, parent, socio_id, ced, nom, tel, plan, monto, met, ref,
                         f_ing, f_venc, recargar):
        form = ctk.CTkFrame(parent, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=20, pady=15)

        def label(txt):
            ctk.CTkLabel(form, text=txt, font=("Inter", 11, "bold"),
                         text_color=COLORS["text_muted"]).pack(anchor="w", pady=(6, 0))

        label("Cédula *")
        ent_ced = ctk.CTkEntry(form, fg_color=COLORS["sidebar"], border_color=COLORS["border"])
        ent_ced.pack(fill="x")
        ent_ced.insert(0, ced or "")
        solo_digitos_entry(ent_ced)

        label("Nombre completo *")
        ent_nom = ctk.CTkEntry(form, fg_color=COLORS["sidebar"], border_color=COLORS["border"])
        ent_nom.pack(fill="x")
        ent_nom.insert(0, nom or "")

        label("Teléfono WhatsApp *")
        ent_tel = ctk.CTkEntry(form, fg_color=COLORS["sidebar"], border_color=COLORS["border"])
        ent_tel.pack(fill="x")
        ent_tel.insert(0, tel or "")
        solo_digitos_entry(ent_tel)

        label("Plan")
        ent_monto = ctk.CTkEntry(form, fg_color=COLORS["sidebar"], border_color=COLORS["border"])

        def al_cambiar_plan(choice):
            ent_monto.delete(0, "end")
            ent_monto.insert(0, str(self._precio_plan(choice)))

        cb_plan = ctk.CTkComboBox(form, values=list(self.PRECIOS.keys()), command=al_cambiar_plan,
                                  state="readonly",
                                  fg_color=COLORS["sidebar"], border_color=COLORS["border"],
                                  button_color=COLORS["accent_primary"],
                                  button_hover_color=COLORS["accent_hover"])
        cb_plan.pack(fill="x")
        cb_plan.set(plan or "Plan Mensual")

        label("Monto ($)")
        ent_monto.pack(fill="x")
        ent_monto.insert(0, str(monto if monto is not None else 0))

        label("Método de pago")
        cb_pago = ctk.CTkComboBox(form, values=self.METODOS, state="readonly",
                                  fg_color=COLORS["sidebar"], border_color=COLORS["border"],
                                  button_color=COLORS["accent_primary"],
                                  button_hover_color=COLORS["accent_hover"])
        cb_pago.pack(fill="x")
        cb_pago.set(met or "Pago Móvil")

        label("Referencia / Últimos 4 dígitos")
        ent_ref = ctk.CTkEntry(form, fg_color=COLORS["sidebar"], border_color=COLORS["border"])
        ent_ref.pack(fill="x")
        ent_ref.insert(0, ref or "")

        label("Fecha de vencimiento")
        ent_venc = tkcalendar.DateEntry(form, width=12, background=COLORS["card"], foreground=COLORS["text_main"], borderwidth=2, date_pattern="yyyy-mm-dd")
        ent_venc.pack(fill="x", pady=2)
        if f_venc:
            try: ent_venc.set_date(f_venc)
            except: pass

        ctk.CTkLabel(form, text=f"Fecha de ingreso: {f_ing or 'N/A'}",
                     font=("Inter", 10), text_color=COLORS["text_muted"]).pack(anchor="w", pady=(10, 0))

        def guardar():
            c = ent_ced.get().strip()
            n = ent_nom.get().strip()
            t = ent_tel.get().strip()
            p = cb_plan.get()
            m = cb_pago.get()
            r = ent_ref.get().strip()
            fv = ent_venc.get().strip()

            if not validar_cedula(c):
                messagebox.showerror("Error", "Cédula inválida (6–9 dígitos).", parent=parent); return
            if not validar_nombre(n):
                messagebox.showerror("Error", "Nombre inválido (mín. 3 letras, sin números).", parent=parent); return
            if not validar_telefono(t):
                messagebox.showerror("Error", "Teléfono inválido (10–11 dígitos, 04 o 4).", parent=parent); return
            if p not in self.PRECIOS:
                messagebox.showerror("Error", "Plan inválido.", parent=parent); return
            try:
                mn = float(ent_monto.get().strip().replace(",", "."))
                if mn <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Error", "Monto inválido.", parent=parent); return
            if m not in self.METODOS:
                messagebox.showerror("Error", "Método inválido.", parent=parent); return
            if not r:
                messagebox.showerror("Error", "La referencia es obligatoria.", parent=parent); return
            fv_date = parse_fecha(fv)
            if not fv_date:
                messagebox.showerror("Error", "Fecha de vencimiento inválida (YYYY-MM-DD).", parent=parent); return

            dup = self.db.ejecutar("SELECT id FROM socios WHERE cedula=? AND id<>?",
                                   (c, socio_id), fetch=True)
            if dup:
                messagebox.showerror("Error", f"La cédula {c} ya pertenece a otro socio.", parent=parent); return

            # Al guardar cambios, si el usuario le puso una nueva fecha, lo reactivamos automáticamente
            activo = 1 if fv_date >= datetime.date.today() else 0
            self.db.ejecutar(
                "UPDATE socios SET cedula=?, nombre=?, telefono=?, plan=?, monto=?, "
                "metodo_pago=?, referencia=?, fecha_vencimiento=?, activo=? WHERE id=?",
                (c, n, t, p, mn, m, r, fv_date.strftime("%Y-%m-%d"), activo, socio_id))
            messagebox.showinfo("Éxito", "Datos actualizados.", parent=parent)
            self.cargar_tabla(self.search_entry.get())
            self.actualizar_kpis()
            recargar("Datos")

        ctk.CTkButton(form, text="Guardar Cambios", command=guardar,
                      fg_color=COLORS["accent_success"], hover_color="#059669").pack(pady=15)

    def _build_tab_progreso(self, parent, socio_id, recargar):
        medidas = self.db.ejecutar(
            "SELECT id, fecha, peso, altura, notas FROM medidas WHERE socio_id=? "
            "ORDER BY fecha ASC, id ASC", (socio_id,), fetch=True)

        summary = ctk.CTkFrame(parent, fg_color=COLORS["sidebar"], corner_radius=8)
        summary.pack(fill="x", padx=15, pady=(10, 8))

        if medidas:
            peso_ini = medidas[0][2]
            peso_act = medidas[-1][2]
            altura_act = medidas[-1][3]
            delta = (peso_act or 0) - (peso_ini or 0)
            signo = "▲" if delta > 0 else ("▼" if delta < 0 else "=")
            color = COLORS["accent_danger"] if delta > 0 else (COLORS["accent_success"] if delta < 0 else COLORS["text_main"])
            imc = (peso_act / ((altura_act / 100) ** 2)) if altura_act else 0
            txt = (f"Peso inicial: {peso_ini:.1f} kg   →   Actual: {peso_act:.1f} kg   "
                   f"({signo} {abs(delta):.1f} kg)   |   IMC actual: {imc:.1f}   |   "
                   f"{len(medidas)} registro(s)")
            lbl = ctk.CTkLabel(summary, text=txt, font=("Inter", 12, "bold"))
            lbl.pack(pady=10, padx=12, anchor="w")
        else:
            ctk.CTkLabel(summary, text="Sin registros de progreso aún.",
                         font=("Inter", 12), text_color=COLORS["text_muted"]).pack(pady=10, padx=12, anchor="w")

        frame = ctk.CTkFrame(parent, fg_color=COLORS["sidebar"], corner_radius=8)
        frame.pack(fill="both", expand=True, padx=15, pady=(0, 8))

        cols = ("Fecha", "Peso (kg)", "Altura (cm)", "IMC", "Notas")
        tree = ttk.Treeview(frame, columns=cols, show="headings")
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, anchor="center", width=110)
        tree.column("Notas", width=220, anchor="w")
        tree.pack(fill="both", expand=True, padx=5, pady=5)

        for m in reversed(medidas):
            _, fecha, peso, altura, notas = m
            imc = (peso / ((altura / 100) ** 2)) if altura else 0
            tree.insert("", "end", values=(fecha, f"{peso:.1f}", f"{altura:.0f}",
                                          f"{imc:.1f}", notas or "-"))
        if medidas:
            fig, ax = plt.subplots(figsize=(5, 2), dpi=100)
            fig.patch.set_facecolor(COLORS["sidebar"])
            ax.set_facecolor(COLORS["sidebar"])
            ax.spines['bottom'].set_color(COLORS["text_muted"])
            ax.spines['left'].set_color(COLORS["text_muted"])
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.tick_params(axis='x', colors=COLORS["text_muted"])
            ax.tick_params(axis='y', colors=COLORS["text_muted"])
            
            fechas = [m[1] for m in medidas]
            pesos = [m[2] for m in medidas]
            ax.plot(fechas, pesos, marker='o', color=COLORS["accent_primary"], linewidth=2)
            ax.set_title("Evolucion del Peso", color=COLORS["text_main"], fontsize=10)
            
            canvas = FigureCanvasTkAgg(fig, master=frame)
            canvas.draw()
            canvas.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)

        def nueva():
            self.ventana_nueva_medida(socio_id, on_success=lambda: recargar("Progreso"))

        ctk.CTkButton(parent, text="+ Nueva Medida", command=nueva,
                      fg_color=COLORS["accent_primary"], text_color=COLORS["text_dark"],
                      hover_color=COLORS["accent_hover"]).pack(pady=(0, 10), padx=15, fill="x")

    def ventana_nueva_medida(self, socio_id, on_success=None):
        last = self.db.ejecutar(
            "SELECT altura FROM medidas WHERE socio_id=? ORDER BY fecha DESC, id DESC LIMIT 1",
            (socio_id,), fetch=True)
        last_altura = last[0][0] if last else 0

        top = ctk.CTkToplevel(self)
        top.title("Nueva Medida")
        top.geometry("440x440")
        top.configure(fg_color=COLORS["bg_main"])
        top.transient(self)

        ctk.CTkLabel(top, text="Registrar Medida Corporal",
                     font=("Inter", 16, "bold"), text_color=COLORS["text_main"]).pack(pady=(15, 10))

        ctk.CTkLabel(top, text="Peso (kg) *", font=("Inter", 11, "bold"),
                     text_color=COLORS["text_muted"]).pack(anchor="w", padx=30)
        ent_peso = ctk.CTkEntry(top, fg_color=COLORS["card"], border_color=COLORS["border"])
        ent_peso.pack(fill="x", padx=30, pady=(0, 6))

        ctk.CTkLabel(top, text="Altura (cm) *", font=("Inter", 11, "bold"),
                     text_color=COLORS["text_muted"]).pack(anchor="w", padx=30)
        ent_altura = ctk.CTkEntry(top, fg_color=COLORS["card"], border_color=COLORS["border"])
        ent_altura.pack(fill="x", padx=30, pady=(0, 6))
        if last_altura:
            ent_altura.insert(0, f"{last_altura:.0f}")

        ctk.CTkLabel(top, text="Notas (opcional)", font=("Inter", 11, "bold"),
                     text_color=COLORS["text_muted"]).pack(anchor="w", padx=30)
        txt_notas = ctk.CTkTextbox(top, height=70, fg_color=COLORS["card"],
                                   border_color=COLORS["border"], border_width=1)
        txt_notas.pack(fill="x", padx=30, pady=(0, 6))

        def guardar():
            try:
                peso = float(ent_peso.get().strip().replace(",", "."))
                if not (10 <= peso <= 400):
                    raise ValueError
            except ValueError:
                messagebox.showerror("Error", "Peso inválido (10–400 kg).", parent=top); return
            try:
                altura = float(ent_altura.get().strip().replace(",", "."))
                if not (50 <= altura <= 250):
                    raise ValueError
            except ValueError:
                messagebox.showerror("Error", "Altura inválida (50–250 cm).", parent=top); return
            notas = txt_notas.get("1.0", "end").strip()

            self.db.ejecutar(
                "INSERT INTO medidas (socio_id, fecha, peso, altura, notas) VALUES (?,?,?,?,?)",
                (socio_id, datetime.date.today().strftime("%Y-%m-%d"), peso, altura, notas))
            top.destroy()
            if on_success:
                on_success()

        ctk.CTkButton(top, text="Guardar Medida", command=guardar,
                      fg_color=COLORS["accent_success"], hover_color="#059669").pack(pady=15)

    def _build_tab_pagos(self, parent, socio_id):
        frame = ctk.CTkFrame(parent, fg_color=COLORS["sidebar"], corner_radius=8)
        frame.pack(fill="both", expand=True, padx=15, pady=15)

        cols = ("Fecha / Hora", "Concepto", "Monto", "Método", "Ref / PagoMóvil")
        tree = ttk.Treeview(frame, columns=cols, show="headings")
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, anchor="center", width=120)
        tree.column("Concepto", width=180)
        tree.pack(fill="both", expand=True, padx=5, pady=5)

        for p in self.db.ejecutar(
                "SELECT fecha, concepto, monto, metodo_pago, referencia FROM pagos "
                "WHERE socio_id=? ORDER BY id DESC", (socio_id,), fetch=True):
            tree.insert("", "end", values=(p[0], p[1], f"${(p[2] or 0):.2f}",
                                          p[3] or "-", p[4] or "-"))
        def generar_pdf():
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("Atención", "Selecciona un pago de la tabla.", parent=parent)
                return
            vals = tree.item(sel[0], "values")
            fecha, concepto, monto, metodo, ref = vals
            
            socio_data = self.db.ejecutar("SELECT nombre, cedula FROM socios WHERE id=?", (socio_id,), fetch=True)[0]
            nombre, cedula = socio_data[0], socio_data[1]
            
            archivo = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")], initialfile=f"Recibo_{cedula}_{ref}.pdf", parent=parent)
            if not archivo: return
            
            try:
                pdf = FPDF()
                pdf.add_page()
                pdf.set_font("Arial", "B", 16)
                pdf.cell(0, 10, "MARVIN GYM", ln=True, align="C")
                pdf.set_font("Arial", size=12)
                pdf.cell(0, 10, "Recibo de Pago", ln=True, align="C")
                pdf.ln(10)
                pdf.cell(0, 10, f"Socio: {nombre}", ln=True)
                pdf.cell(0, 10, f"Cedula: {cedula}", ln=True)
                pdf.cell(0, 10, f"Fecha: {fecha}", ln=True)
                pdf.cell(0, 10, f"Concepto: {concepto}", ln=True)
                pdf.cell(0, 10, f"Metodo: {metodo} | Ref: {ref}", ln=True)
                pdf.set_font("Arial", "B", 14)
                pdf.cell(0, 10, f"Monto: {monto}", ln=True)
                pdf.output(archivo)
                messagebox.showinfo("Éxito", f"Recibo guardado en:\n{archivo}", parent=parent)
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo crear PDF: {e}", parent=parent)

        ctk.CTkButton(parent, text="🧾 Generar Recibo (PDF)", command=generar_pdf,
                      fg_color="transparent", border_width=1, border_color=COLORS["text_muted"],
                      text_color=COLORS["text_main"], width=200).pack(pady=5)

    def _build_tab_asist(self, parent, socio_id):
        frame = ctk.CTkFrame(parent, fg_color=COLORS["sidebar"], corner_radius=8)
        frame.pack(fill="both", expand=True, padx=15, pady=15)

        cols = ("Fecha / Hora",)
        tree = ttk.Treeview(frame, columns=cols, show="headings")
        tree.heading(cols[0], text="Fecha / Hora de acceso")
        tree.column(cols[0], anchor="center", width=200)
        tree.pack(fill="both", expand=True, padx=5, pady=5)

        for a in self.db.ejecutar(
                "SELECT fecha_hora FROM asistencias WHERE socio_id=? ORDER BY id DESC LIMIT 200",
                (socio_id,), fetch=True):
            tree.insert("", "end", values=(a[0],))

    # ------------------------------------------------------------------
    #  ASISTENCIAS GLOBAL
    # ------------------------------------------------------------------
    def ventana_asistencias(self):
        top = ctk.CTkToplevel(self)
        top.title("Asistencias recientes")
        top.geometry("720x460")
        top.configure(fg_color=COLORS["bg_main"])
        top.transient(self)

        ctk.CTkLabel(top, text="🕒 Últimas 200 Asistencias",
                     font=("Inter", 16, "bold"), text_color=COLORS["text_main"]).pack(pady=10)

        frame = ctk.CTkFrame(top, fg_color=COLORS["card"])
        frame.pack(fill="both", expand=True, padx=20, pady=10)

        cols = ("Fecha / Hora", "Cédula", "Nombre", "Plan")
        tree = ttk.Treeview(frame, columns=cols, show="headings")
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, anchor="center", width=140)
        tree.pack(fill="both", expand=True, padx=5, pady=5)

        rows = self.db.ejecutar("""
            SELECT a.fecha_hora, COALESCE(s.cedula, '-'), s.nombre, COALESCE(s.plan, '-')
            FROM asistencias a JOIN socios s ON s.id = a.socio_id
            ORDER BY a.fecha_hora DESC LIMIT 200
        """, fetch=True)
        for r in rows:
            tree.insert("", "end", values=r)

    # ------------------------------------------------------------------
    #  RESPALDOS (Super Usuario)
    # ------------------------------------------------------------------
    def ventana_respaldos(self):
        top = ctk.CTkToplevel(self)
        top.title("Gestión de Respaldos")
        top.geometry("420x350")
        top.configure(fg_color=COLORS["bg_main"])
        top.transient(self)

        ctk.CTkLabel(top, text="Respaldos de Base de Datos",
                     font=("Inter", 16, "bold"), text_color=COLORS["text_main"]).pack(pady=(20, 10))
        ctk.CTkLabel(top, text="Genera copias de seguridad de la información\n"
                              "o restaura la base de datos desde un archivo previo.",
                     font=("Inter", 11), text_color=COLORS["text_muted"], justify="center").pack(pady=(0, 20))

        def crear_backup():
            nombre = f"marvin_gym_backup_{datetime.datetime.now():%Y-%m-%d_%H-%M-%S}.db"
            dest = filedialog.asksaveasfilename(
                defaultextension=".db",
                filetypes=[("Base de Datos SQLite", "*.db"), ("Todos los archivos", "*.*")],
                initialfile=nombre, parent=top)
            if not dest:
                return
            src = dst = None
            try:
                src = sqlite3.connect(self.db.db_name)
                dst = sqlite3.connect(dest)
                src.backup(dst)
                messagebox.showinfo("Éxito", f"Respaldo creado en:\n{dest}", parent=top)
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo crear el respaldo:\n{e}", parent=top)
            finally:
                if dst:
                    dst.close()
                if src:
                    src.close()

        def restaurar_backup():
            if not messagebox.askyesno("Atención",
                    "Al restaurar un respaldo se sobrescribirán todos los datos actuales.\n¿Continuar?",
                    parent=top):
                return
            origen = filedialog.askopenfilename(
                filetypes=[("Base de Datos SQLite", "*.db"), ("Todos los archivos", "*.*")], parent=top)
            if not origen:
                return
            try:
                test = sqlite3.connect(origen)
                try:
                    test.execute("SELECT COUNT(*) FROM socios").fetchone()
                finally:
                    test.close()
                shutil.copy2(self.db.db_name, self.db.db_name + ".before_restore")
                shutil.copy2(origen, self.db.db_name)
                messagebox.showinfo("Éxito",
                    "Base de datos restaurada. La aplicación se actualizará.", parent=top)
                top.destroy()
                self.cargar_tabla(self.search_entry.get())
                self.actualizar_kpis()
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo restaurar el respaldo:\n{e}", parent=top)

        btn = {"height": 42, "corner_radius": 8, "font": ("Inter", 13, "bold")}
        ctk.CTkButton(top, text="💾 Crear Copia de Seguridad", command=crear_backup,
                      fg_color=COLORS["accent_primary"], text_color=COLORS["text_dark"],
                      hover_color=COLORS["accent_hover"], **btn).pack(fill="x", padx=40, pady=8)
        ctk.CTkButton(top, text="📂 Restaurar Copia de Seguridad", command=restaurar_backup,
                      fg_color=COLORS["accent_warning"], text_color=COLORS["text_dark"],
                      hover_color="#D97706", **btn).pack(fill="x", padx=40, pady=8)

    # ------------------------------------------------------------------
    #  CONFIG
    # ------------------------------------------------------------------
    def ventana_config(self):
        top = ctk.CTkToplevel(self)
        top.title("Ajustes del Sistema")
        top.geometry("520x420")
        top.configure(fg_color=COLORS["bg_main"])
        top.transient(self)

        config = self.db.get_config()

        ctk.CTkLabel(top, text="Ajustes del Sistema",
                     font=("Inter", 16, "bold"), text_color=COLORS["text_main"]).pack(pady=10)

        ctk.CTkLabel(top, text="Días previos de alerta por vencer:",
                     font=("Inter", 11, "bold"), text_color=COLORS["text_muted"]).pack(anchor="w", padx=30)
        ent_dias = ctk.CTkEntry(top, fg_color=COLORS["card"], border_color=COLORS["border"])
        ent_dias.pack(fill="x", padx=30, pady=(0, 6))
        ent_dias.insert(0, config.get("dias_alerta", "3"))

        ctk.CTkLabel(top, text="Cambiar Clave Admin (vacío = sin cambios):",
                     font=("Inter", 11, "bold"), text_color=COLORS["text_muted"]).pack(anchor="w", padx=30, pady=(8, 0))
        ent_new_pass = ctk.CTkEntry(top, placeholder_text="Nueva contraseña...", show="*",
                                    fg_color=COLORS["card"], border_color=COLORS["border"])
        ent_new_pass.pack(fill="x", padx=30, pady=(0, 6))

        ctk.CTkLabel(top, text="Plantilla mensaje WhatsApp ({nombre}, {plan}, {vencimiento}):",
                     font=("Inter", 11, "bold"), text_color=COLORS["text_muted"]).pack(anchor="w", padx=30)
        ent_plantilla = ctk.CTkEntry(top, fg_color=COLORS["card"], border_color=COLORS["border"])
        ent_plantilla.pack(fill="x", padx=30, pady=(0, 6))
        ent_plantilla.insert(0, config.get("wa_plantilla", ""))

        def guardar():
            try:
                dias = int(ent_dias.get().strip())
                if dias < 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Error", "Días de alerta debe ser un entero ≥ 0.", parent=top); return
            self.db.set_config("dias_alerta", dias)
            self.db.set_config("wa_plantilla", ent_plantilla.get().strip())
            nueva = ent_new_pass.get().strip()
            if nueva:
                self.db.cambiar_admin_pass(nueva)
            messagebox.showinfo("Éxito", "Ajustes guardados.", parent=top)
            top.destroy()
            self.cargar_tabla(self.search_entry.get())
            self.actualizar_kpis()

        ctk.CTkButton(top, text="Guardar Cambios", command=guardar,
                      fg_color=COLORS["accent_primary"], text_color=COLORS["text_dark"],
                      hover_color=COLORS["accent_hover"]).pack(pady=15)

    # ------------------------------------------------------------------
    #  WHATSAPP
    # ------------------------------------------------------------------
    def enviar_whatsapp(self):
        if not self.selected_socio_id:
            messagebox.showwarning("Atención", "Selecciona un socio en la tabla.")
            return
        data = self.db.ejecutar(
            "SELECT nombre, telefono, plan, fecha_vencimiento FROM socios WHERE id=?",
            (self.selected_socio_id,), fetch=True)
        if not data:
            return
        nom, tel, plan, f_venc = data[0]
        if not tel:
            messagebox.showerror("Error", "El socio no posee número telefónico registrado.")
            return
        config = self.db.get_config()
        plantilla = config.get("wa_plantilla", "Hola {nombre}, tu membresía vence el {vencimiento}.")
        msg = plantilla.format(nombre=nom, plan=plan or "-", vencimiento=f_venc or "-")
        exito, mensaje = WhatsAppManager.enviar_mensaje(tel, msg)
        (messagebox.showinfo if exito else messagebox.showerror)("WhatsApp", mensaje)

    # ------------------------------------------------------------------
    #  ELIMINAR / DESACTIVAR (Sin contraseña)
    # ------------------------------------------------------------------
    def eliminar_socio(self):
        if not self.selected_socio_id:
            messagebox.showwarning("Atención", "Selecciona un socio para eliminar.")
            return
            
        dialog = ctk.CTkInputDialog(text="Ingrese clave de Administrador para eliminar:", title="Seguridad")
        clave = dialog.get_input()
        if not clave or not self.db.verificar_admin_pass(clave):
            messagebox.showerror("Denegado", "Clave incorrecta o cancelada.")
            return

        row = self.db.ejecutar("SELECT nombre FROM socios WHERE id=?", (self.selected_socio_id,), fetch=True)
        if not row:
            return
        nombre = row[0][0]

        msg = (f"¿Qué deseas hacer con {nombre}?\n\n"
               f"SÍ = Borrar definitivamente (Se perderá su historial).\n"
               f"NO = Solo desactivar (Se oculta pero mantiene historial de caja).")
        
        opcion = messagebox.askyesnocancel("Opciones de Socio", msg)
        
        if opcion is True: # Yes -> Delete
            if messagebox.askyesno("Confirmar Borrado", f"¿Estás SEGURO de borrar a {nombre} y todo su historial?"):
                self.db.ejecutar("DELETE FROM socios WHERE id=?", (self.selected_socio_id,))
                messagebox.showinfo("Eliminado", f"{nombre} fue eliminado del sistema.")
            else:
                return
        elif opcion is False: # No -> Deactivate
            self.db.ejecutar("UPDATE socios SET activo=0 WHERE id=?", (self.selected_socio_id,))
            messagebox.showinfo("Desactivado", f"{nombre} fue desactivado. Su historial se conserva.")
        else: # Cancel -> Do nothing
            return

        self.selected_socio_id = None
        self.cargar_tabla(self.search_entry.get())
        self.actualizar_kpis()


def _configurar_logging():
    log_dir = Path(platformdirs.user_log_dir("MarvinGym", "MarvinGym"))
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", handlers=[handler, logging.StreamHandler()])
    logging.info("=== Marvin Gym iniciado ===")

if __name__ == "__main__":
    _configurar_logging()
    try:
        app = GymApp()
        app.mainloop()
    except Exception:
        logging.exception("Fallo fatal en la app")
        raise
