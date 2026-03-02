from __future__ import annotations

import json
import os
import platform
import queue
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import customtkinter as ctk
import webbrowser
from pathlib import Path
from tkinter import colorchooser, filedialog, simpledialog, ttk
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except Exception:
    TkinterDnD = None
    DND_FILES = None


if TkinterDnD is not None:
    class CTkinterDnD(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._dnd_ready = False
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self._dnd_ready = True
            except Exception:
                self._dnd_ready = False
else:
    class CTkinterDnD(ctk.CTk):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._dnd_ready = False


from ble_client import BleChatClient
from chat_sessions import ChatSessionsStore
from context_store import ContextStore
from pdf_context import PdfContextEngine

try:
    from PIL import ImageGrab, Image as PILImage, ImageDraw
except Exception:
    ImageGrab = None
    PILImage = None
    ImageDraw = None

try:
    import pystray
except Exception:
    pystray = None

try:
    import objc
except Exception:
    objc = None

try:
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSApplicationActivationPolicyRegular,
        NSImage,
        NSMenu,
        NSMenuItem,
        NSStatusBar,
        NSVariableStatusItemLength,
    )
except Exception:
    NSApplication = None
    NSApplicationActivationPolicyAccessory = None
    NSApplicationActivationPolicyRegular = None
    NSImage = None
    NSMenu = None
    NSMenuItem = None
    NSStatusBar = None
    NSVariableStatusItemLength = None

try:
    from Foundation import NSObject
except Exception:
    NSObject = None

try:
    from ApplicationServices import (
        AXIsProcessTrusted,
        AXIsProcessTrustedWithOptions,
        kAXTrustedCheckOptionPrompt,
    )
except Exception:
    AXIsProcessTrusted = None
    AXIsProcessTrustedWithOptions = None
    kAXTrustedCheckOptionPrompt = None

try:
    from Quartz import CGPreflightScreenCaptureAccess, CGRequestScreenCaptureAccess
except Exception:
    CGPreflightScreenCaptureAccess = None
    CGRequestScreenCaptureAccess = None

try:
    from CoreBluetooth import CBCentralManager
except Exception:
    CBCentralManager = None

try:
    from pynput import keyboard as pynput_keyboard
except Exception:
    pynput_keyboard = None

try:
    import certifi
except Exception:
    certifi = None

MODEL_PRESETS = [
    "phone-default",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-pro-preview-03-25",
    "gemini-2.5-flash-preview-04-17",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.0-pro-exp",
]

APP_VERSION = "0.1.8"
GITHUB_REPO = "MN-company/bluetooth-gemini-chat"


if objc is not None and NSObject is not None:
    class _MacMenuActionTarget(NSObject):
        def initWithCallback_(self, callback: Any) -> Any:
            self = objc.super(_MacMenuActionTarget, self).init()
            if self is None:
                return None
            self._callback = callback
            return self

        def onAction_(self, _sender: Any) -> None:
            try:
                cb = getattr(self, "_callback", None)
                if cb is not None:
                    cb()
            except Exception:
                pass
else:
    _MacMenuActionTarget = None


class DesktopChatApp:
    def __init__(self) -> None:
        self.root = CTkinterDnD()
        self.root.title("Gemini BLE Chat")
        self.root.geometry("1240x780")
        self.root.minsize(300, 400)
        self._dnd_enabled = False
        self._dnd_issue_note = ""
        if getattr(self.root, "_dnd_ready", False) and DND_FILES is not None:
            try:
                self.root.drop_target_register(DND_FILES)
                self.root.dnd_bind("<<Drop>>", self._on_file_drop)
                self._dnd_enabled = True
            except Exception as exc:
                self._dnd_issue_note = str(exc)
        else:
            self._dnd_issue_note = "tkinterdnd2/tkdnd non disponibile"

        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.client = BleChatClient(self.events.put)
        self.client.start()

        self.devices: list[dict[str, str]] = []
        self.connected = False
        self.selected_image_path: str | None = None
        self.selected_pdf_paths: list[str] = []
        self.pdf_context_engine = PdfContextEngine()

        sessions_path = str(Path(__file__).with_name("chat_sessions.json"))
        self.sessions_store = ChatSessionsStore(sessions_path)
        self.active_session_id = self.sessions_store.active_session_id

        self._pending_request_session: dict[str, str] = {}
        self._pending_request_order: list[str] = []
        self._streaming_preview_by_session: dict[str, str] = {}
        self._streaming_thought_by_session: dict[str, str] = {}
        self._session_ids_in_view: list[str] = []
        self._last_link_state = "unknown"
        self._runtime_bridge_dir = Path.home() / ".gemini_ble"
        self._runtime_bridge_dir.mkdir(parents=True, exist_ok=True)
        self._quick_inbox_path = self._runtime_bridge_dir / "quick_inbox.jsonl"
        self._quick_inbox_offset = 0
        self._md_link_seq = 0
        self._md_link_urls: dict[str, str] = {}
        self._pip_mode_active = False
        self._pre_pip_geometry = ""
        self._is_macos = platform.system().lower() == "darwin"
        self._overlay_listener: Any | None = None
        self._overlay_hotkey = "Apple Shortcut (Cmd+Shift+G)" if self._is_macos else "Ctrl+Shift+G"
        self._overlay_request_ids: set[str] = set()
        self._overlay_image_paths_by_request: dict[str, str] = {}
        self._overlay_started_at: dict[str, float] = {}
        self._overlay_last_update_at: dict[str, float] = {}
        self._overlay_timeout_seconds = 95.0
        self._overlay_last_present_at = 0.0
        self._overlay_hide_after_id: str | None = None
        self._overlay_window: tk.Toplevel | None = None
        self._overlay_message_widget: tk.Message | None = None
        self._overlay_text_var = tk.StringVar(value="")
        self._toggle_flag_path = self._runtime_bridge_dir / "toggle.flag"
        self._toggle_flag_mtime = 0.0
        self._clipboard_flag_path = self._runtime_bridge_dir / "clipboard.flag"
        self._clipboard_flag_mtime = 0.0

        self._settings_path = Path(__file__).with_name("settings.json")
        _saved = self._load_settings()
        self.system_instructions_var = tk.StringVar(value=_saved.get("system_instructions", ""))
        self.pinned_pdf_paths: list[str] = _saved.get("pinned_pdf_paths", [])
        self._last_connected_address = str(_saved.get("last_connected_address", "")).strip() or None
        self._auto_connect_on_start = bool(_saved.get("auto_connect_on_start", True))
        self._auto_retry_known_device = bool(_saved.get("auto_retry_known_device", True))
        self._auto_check_updates = bool(_saved.get("auto_check_updates", True))
        self._close_to_background_on_close = bool(_saved.get("close_to_background_on_close", self._is_macos))
        self._menu_bar_mode_enabled = bool(_saved.get("menu_bar_mode_enabled", self._is_macos))
        self._hide_dock_icon_enabled = bool(_saved.get("hide_dock_icon_enabled", self._is_macos))
        self._overlay_bg_color = self._normalize_hex_color(_saved.get("overlay_bg_color"), "#0f172a")
        self._overlay_width = self._parse_int_setting(_saved.get("overlay_width"), 460, 320, 1280)
        self._overlay_height = self._parse_int_setting(_saved.get("overlay_height"), 220, 160, 900)
        self._overlay_resizable = bool(_saved.get("overlay_resizable", True))
        self._tray_icon: Any | None = None
        self._tray_thread: threading.Thread | None = None
        self._mac_status_item: Any | None = None
        self._mac_status_menu: Any | None = None
        self._mac_status_targets: list[Any] = []
        self._permissions_dialog: ctk.CTkToplevel | None = None
        self._bluetooth_probe_manager: Any | None = None
        self._macos_policy_applied = False
        if self._is_macos:
            self._menu_bar_available = (
                NSStatusBar is not None
                and NSMenu is not None
                and NSMenuItem is not None
                and NSApplication is not None
                and _MacMenuActionTarget is not None
            )
        else:
            self._menu_bar_available = pystray is not None and PILImage is not None

        self._context_store = ContextStore(Path(__file__).parent)
        self._active_container_id: str | None = None
        self._selected_container_idx: int | None = None
        self._container_transfer_request_id: str | None = None
        self._container_transfer_container_id_by_request: dict[str, str] = {}
        self._remote_containers: list[dict[str, Any]] = []
        self._transfer_dialog: ctk.CTkToplevel | None = None
        self._transfer_progress_var: tk.DoubleVar | None = None
        self._transfer_label_var: tk.StringVar | None = None
        self._transfer_started_time: float = 0.0

        self._configure_theme()
        self._build_ui()
        if not self._dnd_enabled:
            if platform.system().lower() == "linux":
                self._append_log("System", "Drag & drop disabilitato su Linux: usa i pulsanti Attach/Add PDF.")
            elif self._dnd_issue_note:
                self._append_log("System", f"Drag & drop disabilitato: {self._dnd_issue_note}")
        self.client.set_auto_reconnect(self._auto_retry_known_device)
        self._start_menu_bar_icon_if_needed()
        self._apply_macos_activation_policy()
        self._refresh_sessions_list(self.active_session_id)
        self._render_active_chat()
        self._refresh_memory_label()
        self._refresh_context_preview()
        self._auto_install_quick_action()
        self._start_overlay_hotkey_listener()
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)
        self.root.after(100, self._poll_events)
        self.root.after(1200, self._maybe_auto_connect_on_start)
        self.root.after(1500, self._maybe_show_permissions_onboarding)
        if self._auto_check_updates:
            self.root.after(2200, lambda: self.on_check_updates(background=True))

    def _configure_theme(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")


    def _build_ui(self) -> None:
        # Main grid setup: 2 rows (Header, Content), 2 cols (Sidebar, Chat)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(1, weight=1)

        # --- HEADER (Row 0, spans 2 cols originally, but we'll put it in a frame that spans) ---
        self.header_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        self.header_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=8)
        self.header_frame.columnconfigure(1, weight=1) # filler
        
        # Header Left: Model, Add PDF, Clipboard, Screenshot
        header_left = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        header_left.grid(row=0, column=0, sticky="w")
        
        self.model_var = tk.StringVar(value=MODEL_PRESETS[0])
        self.model_combo = ctk.CTkComboBox(
            header_left,
            variable=self.model_var,
            values=MODEL_PRESETS,
            state="readonly",
            command=lambda _: self._refresh_context_preview(),
            width=160
        )
        self.model_combo.pack(side=tk.LEFT, padx=(0, 10))
        
        ctk.CTkButton(header_left, text="ADD PDF", command=self.on_add_pdf, fg_color="transparent", border_width=1, hover_color="#333333", text_color="#e0e0e0", width=80).pack(side=tk.LEFT, padx=(0, 6))
        ctk.CTkButton(header_left, text="CLIPBOARD", command=self.on_clipboard_send, fg_color="transparent", border_width=1, hover_color="#333333", text_color="#e0e0e0", width=80).pack(side=tk.LEFT, padx=(0, 6))
        ctk.CTkButton(header_left, text="SCREENSHOT", command=self.on_quick_screenshot, fg_color="transparent", border_width=1, hover_color="#333333", text_color="#e0e0e0", width=80).pack(side=tk.LEFT, padx=(0, 6))
        ctk.CTkButton(header_left, text="UPDATE", command=lambda: self.on_check_updates(background=False), fg_color="transparent", border_width=1, hover_color="#333333", text_color="#e0e0e0", width=70).pack(side=tk.LEFT, padx=(0, 6))
        ctk.CTkButton(header_left, text="⚙️", command=self.on_open_settings, fg_color="transparent", border_width=1, hover_color="#333333", text_color="#e0e0e0", width=30).pack(side=tk.LEFT)

        # Header Right: Device Name / Connection info
        header_right = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        header_right.grid(row=0, column=2, sticky="e")
        
        self.status_var = tk.StringVar(value="Not connected")
        self.link_var = tk.StringVar(value="Link: n/a")
        
        status_card = ctk.CTkFrame(header_right, border_width=1, border_color="#333333", fg_color="#1c1c1c")
        status_card.pack(side=tk.RIGHT)
        
        ctk.CTkLabel(status_card, textvariable=self.status_var, font=("Avenir", 12, "bold")).pack(anchor=tk.E, padx=10, pady=(6, 0))
        ctk.CTkLabel(status_card, textvariable=self.link_var, font=("Avenir", 10), text_color="#a1a1a1").pack(anchor=tk.E, padx=10, pady=(0, 6))


        # --- SIDEBAR (Row 1, Col 0) ---
        self.sidebar_frame = ctk.CTkFrame(self.root, width=310, fg_color="transparent")
        self.sidebar_frame.grid(row=1, column=0, sticky="ns", padx=(12, 6), pady=(0, 12))
        self.sidebar_frame.grid_propagate(False)
        
        # Chats header
        chats_header = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        chats_header.pack(fill=tk.X, pady=(0, 6))
        ctk.CTkLabel(chats_header, text="chats", font=("Avenir", 18)).pack(side=tk.LEFT)
        ctk.CTkButton(chats_header, text="+", command=self.on_new_chat, width=30, fg_color="transparent", border_width=1, hover_color="#333333", text_color="#e0e0e0").pack(side=tk.RIGHT)

        # Chat Search
        self.search_var = tk.StringVar()
        self.search_entry = ctk.CTkEntry(
            self.sidebar_frame,
            textvariable=self.search_var,
            placeholder_text="Cerca chat...",
            height=28,
            font=("Avenir", 12),
            fg_color="#1c1c1c",
            border_width=1,
            border_color="#333333"
        )
        self.search_entry.pack(fill=tk.X, pady=(0, 6))
        self.search_entry.bind("<KeyRelease>", lambda e: self._refresh_sessions_list(self.active_session_id))

        # Chats listbox
        self.chats_list = tk.Listbox(
            self.sidebar_frame,
            bg="#1c1c1c",
            fg="#e0e0e0",
            selectbackground="#1f538d",
            activestyle=tk.NONE,
            borderwidth=1,
            relief=tk.SOLID,
            highlightthickness=0,
        )
        self.chats_list.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.chats_list.bind("<<ListboxSelect>>", self._on_session_selected)

        # --- Knowledge Base Container Panel ---
        separator = ctk.CTkFrame(self.sidebar_frame, height=1, fg_color="#2a2a2a")
        separator.pack(fill=tk.X, pady=(6, 8))

        kb_header = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        kb_header.pack(fill=tk.X, pady=(0, 4))
        ctk.CTkLabel(kb_header, text="📚 Libreria", font=("Avenir", 14, "bold"), text_color="#b0b0b0").pack(side=tk.LEFT)
        ctk.CTkButton(
            kb_header, text="＋ Nuovo", command=self._on_create_container,
            width=72, height=24, fg_color="#1f538d", hover_color="#2a6bc7",
            text_color="white", font=("Avenir", 11),
        ).pack(side=tk.RIGHT)

        self.container_list = tk.Listbox(
            self.sidebar_frame,
            bg="#181818",
            fg="#cccccc",
            selectbackground="#1e5c1e",
            selectforeground="#ffffff",
            activestyle=tk.NONE,
            borderwidth=1,
            relief=tk.SOLID,
            highlightthickness=0,
            height=5,
            font=("Avenir", 12),
        )
        self.container_list.pack(fill=tk.X, pady=(0, 4))
        self.container_list.bind("<<ListboxSelect>>", self._on_container_list_click)
        self.container_list.bind("<Double-Button-1>", self._on_activate_container)

        # Row 1: Add PDF | Attiva | Upload
        kb_row1 = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        kb_row1.pack(fill=tk.X, pady=(0, 3))
        ctk.CTkButton(
            kb_row1, text="📎 Aggiungi PDF", command=self._on_add_pdf_to_container,
            height=30, fg_color="transparent", border_width=1, border_color="#444",
            hover_color="#2a2a2a", text_color="#e0e0e0", font=("Avenir", 11),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        ctk.CTkButton(
            kb_row1, text="✓ Attiva", command=self._on_activate_container,
            width=68, height=30, fg_color="#1e5c1e", hover_color="#2a7a2a",
            text_color="white", font=("Avenir", 11),
        ).pack(side=tk.LEFT, padx=(0, 3))
        ctk.CTkButton(
            kb_row1, text="📤", command=self._on_upload_container,
            width=34, height=30, fg_color="transparent", border_width=1, border_color="#444",
            hover_color="#2a2a2a", text_color="#d0d0d0", font=("Avenir", 13),
        ).pack(side=tk.LEFT)
        ctk.CTkButton(
            kb_row1, text="☁", command=self._on_sync_remote_containers,
            width=34, height=30, fg_color="transparent", border_width=1, border_color="#444",
            hover_color="#2a2a2a", text_color="#d0d0d0", font=("Avenir", 13),
        ).pack(side=tk.LEFT, padx=(3, 0))

        # Row 2: Active indicator + Delete
        kb_row2 = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        kb_row2.pack(fill=tk.X, pady=(0, 8))
        self._kb_active_label = ctk.CTkLabel(
            kb_row2, text="nessun container attivo",
            font=("Avenir", 10), text_color="#555555",
        )
        self._kb_active_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ctk.CTkButton(
            kb_row2, text="🗑", command=self._on_delete_container,
            width=30, height=24, fg_color="transparent", border_width=1, border_color="#5c1a1a",
            hover_color="#5c1a1a", text_color="#e0e0e0", font=("Avenir", 12),
        ).pack(side=tk.RIGHT)

        self._refresh_container_list()
        
        # Extra tool buttons collapsed at bottom of sidebar since sketch didn't strictly place them
        self._build_button_grid(
            self.sidebar_frame,
            [
                ("Rename", self.on_rename_chat),
                ("Delete", self.on_delete_chat),
                ("Scan", self.on_scan),
                ("Connect", self.on_connect),
                ("Disconnect", self.on_disconnect),
                ("Clear Mem", self.on_clear_memory),
                ("Shot+Ask", self.on_hotkey_overlay_triggered),
                ("Clip+Ask", self.on_hotkey_clipboard_triggered),
            ],
            columns=2,
            pady=(8, 8),
        )
        
        self.devices_list = tk.Listbox(
            self.sidebar_frame, height=3, bg="#1c1c1c", fg="#e0e0e0", selectbackground="#1f538d", activestyle=tk.NONE, borderwidth=1, relief=tk.SOLID, highlightthickness=0,
        )
        self.devices_list.pack(fill=tk.X, pady=(0, 6))
        ctk.CTkLabel(
            self.sidebar_frame,
            text=f"Trigger: {self._overlay_hotkey}",
            text_color="#a1a1a1",
            font=("Avenir", 10),
        ).pack(anchor=tk.W, pady=(0, 6))


        # --- MAIN CHAT AREA (Row 1, Col 1) ---
        self.chat_area_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        self.chat_area_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 12))
        
        # Chat card
        chat_card = ctk.CTkFrame(self.chat_area_frame, fg_color="#1e1e1e", border_width=1, border_color="#333333")
        chat_card.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
        
        self.chat_log = ctk.CTkTextbox(
            chat_card,
            wrap=tk.WORD,
            state="disabled",
            fg_color="transparent",
            text_color="#e0e0e0",
            font=("Avenir", 13)
        )
        self.chat_log.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self._configure_chat_tags()

        # Context indicators & Toggles row
        toggles_row = ctk.CTkFrame(self.chat_area_frame, fg_color="transparent")
        toggles_row.pack(fill=tk.X, pady=(0, 6))
        
        self.web_search_enabled = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(toggles_row, text="WEBSEARCH", variable=self.web_search_enabled, command=self._refresh_context_preview).pack(side=tk.LEFT, padx=(0, 12))
        self.thinking_enabled = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(toggles_row, text="THINKING", variable=self.thinking_enabled, command=self._refresh_context_preview).pack(side=tk.LEFT, padx=(0, 12))
        
        self.pip_enabled = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(toggles_row, text="PiP MODE", variable=self.pip_enabled, command=self._toggle_pip).pack(side=tk.LEFT)
        
        # Hidden variables for thinking budget since they were in older UI
        self.thinking_auto_var = tk.BooleanVar(value=False)
        self.thinking_budget_var = tk.StringVar(value="1024")
        self.show_thoughts_var = tk.BooleanVar(value=True)

        self.context_preview_var = tk.StringVar(value="No active attachments")
        ctk.CTkLabel(toggles_row, textvariable=self.context_preview_var, text_color="#a1a1a1", font=("Avenir", 11)).pack(side=tk.RIGHT)

        # Input Row
        input_row = ctk.CTkFrame(self.chat_area_frame, fg_color="transparent")
        input_row.pack(fill=tk.X)
        
        self.prompt_entry = ctk.CTkTextbox(
            input_row,
            height=60,
            wrap=tk.WORD,
            fg_color="#1e1e1e",
            text_color="#e0e0e0",
            border_width=1,
            border_color="#333333",
            font=("Avenir", 13)
        )
        self.prompt_entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.prompt_entry.bind("<Return>", self._on_prompt_return)
        self.prompt_entry.bind("<KeyRelease>", self._adjust_input_height)
        self.prompt_entry.bind("<Command-BackSpace>", self._on_clear_composer_hotkey)
        self.prompt_entry.bind("<Command-Delete>", self._on_clear_composer_hotkey)

        send_btn = ctk.CTkButton(input_row, text="SEND", command=self.on_send, fg_color="#1e1e1e", border_width=1, border_color="#333333", hover_color="#333333", text_color="#e0e0e0", width=80)
        send_btn.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        self.stop_btn = ctk.CTkButton(
            input_row,
            text="STOP",
            command=self.on_stop_active_request,
            fg_color="#3a1c1c",
            border_width=1,
            border_color="#5a2d2d",
            hover_color="#5a2d2d",
            text_color="#f5d0d0",
            width=72,
            state="disabled",
        )
        self.stop_btn.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))

        # Hidden variables to preserve underlying logic
        self.image_var = tk.StringVar(value="Image: none")
        self.pdf_var = tk.StringVar(value="PDF: none")
        self.memory_var = tk.StringVar(value="")
        
        self.root.bind("<Configure>", self._on_window_resize)
        
        # --- GLOBAL HOTKEYS BINDING ---
        self.root.bind("<Command-n>", lambda e: [self.on_new_chat(), self.prompt_entry.focus()])
        self.root.bind("<Command-r>", lambda e: self.on_rename_chat())
        self.root.bind("<Command-BackSpace>", self._on_global_backspace_hotkey)
        self.root.bind("<Command-k>", lambda e: self.search_entry.focus())
        self.root.bind("<Command-f>", lambda e: self.search_entry.focus())
        
        self._is_compact_mode = False

    def _on_global_backspace_hotkey(self, event) -> str | None:
        if event.widget == self.prompt_entry._textbox:
            self._on_clear_composer_hotkey(event)
            return "break"
        # If we're not inside the textbox, attempt to delete the active chat
        self.on_delete_chat()
        return "break"

    def _load_settings(self) -> dict[str, Any]:
        if not self._settings_path.exists():
            return {}
        try:
            with self._settings_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _parse_int_setting(self, raw: Any, default: int, low: int, high: int) -> int:
        try:
            value = int(raw)
        except Exception:
            value = default
        return max(low, min(high, value))

    def _normalize_hex_color(self, raw: Any, fallback: str) -> str:
        if not isinstance(raw, str):
            return fallback
        value = raw.strip()
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return value
        return fallback

    def _save_settings(self, data: dict[str, Any]) -> None:
        try:
            with self._settings_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _update_settings(self, patch: dict[str, Any]) -> None:
        current = self._load_settings()
        current.update(patch)
        self._save_settings(current)

    def _open_external_url(self, url: str, use_ssl_fallback: bool = False) -> bytes:
        request = urlrequest.Request(url, headers={"User-Agent": f"BluetoothGeminiChat/{APP_VERSION}"})
        contexts: list[ssl.SSLContext | None] = [None]
        if use_ssl_fallback:
            contexts = [self._ssl_context_verified(), self._ssl_context_unverified()]
        for context in contexts:
            try:
                with urlrequest.urlopen(request, timeout=30, context=context) as response:
                    return response.read()
            except Exception:
                if context is not contexts[-1]:
                    continue
                raise
        raise RuntimeError("unreachable")

    def _ssl_context_verified(self) -> ssl.SSLContext | None:
        try:
            if certifi is not None:
                return ssl.create_default_context(cafile=certifi.where())
            return ssl.create_default_context()
        except Exception:
            return None

    def _ssl_context_unverified(self) -> ssl.SSLContext | None:
        try:
            return ssl._create_unverified_context()
        except Exception:
            return None

    def _open_macos_privacy_pane(self, pane_suffix: str) -> None:
        if not self._is_macos:
            return
        targets = [
            f"x-apple.systempreferences:com.apple.preference.security?{pane_suffix}",
            "x-apple.systempreferences:com.apple.preference.security",
            "x-apple.systempreferences:com.apple.settings.PrivacySecurity",
            "/System/Applications/System Settings.app",
        ]
        for target in targets:
            try:
                result = subprocess.run(["open", target], check=False, capture_output=True, text=True)
                if result.returncode == 0:
                    return
            except Exception:
                continue
        self._append_log("Error", "Cannot open macOS privacy settings. Apri manualmente Impostazioni > Privacy e Sicurezza.")

    def _has_screen_recording_permission(self) -> bool | None:
        if not self._is_macos:
            return True
        if CGPreflightScreenCaptureAccess is None:
            return None
        try:
            return bool(CGPreflightScreenCaptureAccess())
        except Exception:
            return None

    def _request_screen_recording_permission(self) -> bool | None:
        if not self._is_macos:
            return True
        if CGRequestScreenCaptureAccess is None:
            return None
        try:
            return bool(CGRequestScreenCaptureAccess())
        except Exception:
            return None

    def _has_accessibility_permission(self) -> bool | None:
        if not self._is_macos:
            return True
        if AXIsProcessTrusted is None:
            return None
        try:
            return bool(AXIsProcessTrusted())
        except Exception:
            return None

    def _request_accessibility_permission(self) -> bool | None:
        if not self._is_macos:
            return True
        if AXIsProcessTrustedWithOptions is None:
            return None
        try:
            options: dict[Any, Any]
            if kAXTrustedCheckOptionPrompt is not None:
                options = {kAXTrustedCheckOptionPrompt: True}
            else:
                options = {"AXTrustedCheckOptionPrompt": True}
            return bool(AXIsProcessTrustedWithOptions(options))
        except Exception:
            return None

    def _bluetooth_authorization_state(self) -> str | None:
        if not self._is_macos:
            return "granted"
        if CBCentralManager is None:
            return None
        try:
            auth_value = int(CBCentralManager.authorization())
            if auth_value == 3:
                return "granted"
            if auth_value == 2:
                return "denied"
            if auth_value == 1:
                return "restricted"
            if auth_value == 0:
                return "not_determined"
            return f"unknown({auth_value})"
        except Exception:
            return None

    def _request_bluetooth_permission(self) -> None:
        if not self._is_macos:
            return
        # Instantiate a central manager once to trigger the OS prompt on first run.
        if CBCentralManager is not None and self._bluetooth_probe_manager is None:
            try:
                self._bluetooth_probe_manager = CBCentralManager.alloc().init()
            except Exception:
                self._bluetooth_probe_manager = None
        try:
            self.client.scan_devices()
        except Exception:
            pass

    def _format_permission_state(self, state: bool | None, label: str) -> str:
        if state is True:
            return f"{label}: OK"
        if state is False:
            return f"{label}: Missing"
        return f"{label}: Unknown"

    def _maybe_show_permissions_onboarding(self) -> None:
        if not self._is_macos:
            return
        settings = self._load_settings()
        if bool(settings.get("permissions_onboarding_done", False)):
            return
        self._show_permissions_onboarding(force=False)

    def _show_permissions_onboarding(self, force: bool = False) -> None:
        if not self._is_macos:
            return
        if self._permissions_dialog is not None and self._permissions_dialog.winfo_exists():
            self._permissions_dialog.lift()
            return
        if not force:
            settings = self._load_settings()
            if bool(settings.get("permissions_onboarding_done", False)):
                return

        from tkinter import messagebox

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Setup permessi macOS")
        dialog.geometry("680x560")
        dialog.transient(self.root)
        dialog.grab_set()
        self._permissions_dialog = dialog

        ctk.CTkLabel(
            dialog,
            text="Primo avvio: abilita i permessi richiesti",
            font=("Avenir", 18, "bold"),
        ).pack(anchor=tk.W, padx=16, pady=(14, 6))
        ctk.CTkLabel(
            dialog,
            text=(
                "L'app usa Bluetooth (bridge), Screen Recording (Shot+Ask) e "
                "Accessibility (shortcut globali/overlay)."
            ),
            justify=tk.LEFT,
            wraplength=640,
            text_color="#b0b0b0",
        ).pack(anchor=tk.W, padx=16, pady=(0, 12))
        ctk.CTkLabel(
            dialog,
            text=(
                "Nota: alcuni prompt macOS compaiono solo una volta. "
                "Se lo stato resta 'Missing', apri Impostazioni e abilita manualmente."
            ),
            justify=tk.LEFT,
            wraplength=640,
            text_color="#8f8f8f",
        ).pack(anchor=tk.W, padx=16, pady=(0, 8))

        screen_state_var = tk.StringVar()
        access_state_var = tk.StringVar()
        bt_state_var = tk.StringVar()
        bt_manual_confirm = tk.BooleanVar(value=False)

        panel = ctk.CTkFrame(dialog, fg_color="#151515", border_width=1, border_color="#2a2a2a")
        panel.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 10))

        def section_row(
            title: str,
            subtitle: str,
            status_var: tk.StringVar,
            request_cmd: Any,
            open_cmd: Any,
        ) -> None:
            row = ctk.CTkFrame(panel, fg_color="transparent")
            row.pack(fill=tk.X, padx=12, pady=(12, 2))
            ctk.CTkLabel(row, text=title, font=("Avenir", 14, "bold")).pack(anchor=tk.W)
            ctk.CTkLabel(
                row,
                text=subtitle,
                justify=tk.LEFT,
                wraplength=620,
                text_color="#b0b0b0",
            ).pack(anchor=tk.W, pady=(0, 4))
            ctk.CTkLabel(row, textvariable=status_var, font=("Avenir", 12)).pack(anchor=tk.W, pady=(0, 4))
            btn_row = ctk.CTkFrame(row, fg_color="transparent")
            btn_row.pack(anchor=tk.W, pady=(0, 2))
            ctk.CTkButton(btn_row, text="Richiedi", width=96, command=request_cmd).pack(side=tk.LEFT, padx=(0, 8))
            ctk.CTkButton(
                btn_row,
                text="Apri Impostazioni",
                width=156,
                command=open_cmd,
                fg_color="transparent",
                border_width=1,
                hover_color="#2a2a2a",
            ).pack(side=tk.LEFT)

        def refresh_states() -> tuple[bool, bool, bool]:
            screen_state = self._has_screen_recording_permission()
            access_state = self._has_accessibility_permission()
            bt_state = self._bluetooth_authorization_state()
            bt_ok = bt_state == "granted" or bt_manual_confirm.get()

            screen_state_var.set(self._format_permission_state(screen_state, "Screen Recording"))
            access_state_var.set(self._format_permission_state(access_state, "Accessibility"))

            if bt_state == "granted":
                bt_manual_confirm.set(True)
                bt_state_var.set("Bluetooth: OK")
            elif bt_state in {"denied", "restricted"}:
                bt_state_var.set(f"Bluetooth: {bt_state}")
            elif bt_state == "not_determined":
                bt_state_var.set("Bluetooth: in attesa autorizzazione")
            else:
                bt_state_var.set("Bluetooth: verifica manuale (premi Richiedi)")

            return (screen_state is True, access_state is True, bt_ok)

        def request_screen_permission() -> None:
            result = self._request_screen_recording_permission()
            if result is not True:
                self._open_macos_privacy_pane("Privacy_ScreenCapture")
            refresh_states()

        def request_access_permission() -> None:
            result = self._request_accessibility_permission()
            if result is not True:
                self._open_macos_privacy_pane("Privacy_Accessibility")
            refresh_states()

        def request_bluetooth_permission() -> None:
            self._request_bluetooth_permission()
            self._open_macos_privacy_pane("Privacy_Bluetooth")
            dialog.after(900, refresh_states)

        section_row(
            "1) Screen Recording",
            "Necessario per Shot+Ask e screenshot area su macOS.",
            screen_state_var,
            request_screen_permission,
            lambda: self._open_macos_privacy_pane("Privacy_ScreenCapture"),
        )
        section_row(
            "2) Accessibility",
            "Necessario per integrazione shortcut globali e overlay affidabile.",
            access_state_var,
            request_access_permission,
            lambda: self._open_macos_privacy_pane("Privacy_Accessibility"),
        )
        section_row(
            "3) Bluetooth",
            "Necessario per scan e connessione BLE col telefono.",
            bt_state_var,
            request_bluetooth_permission,
            lambda: self._open_macos_privacy_pane("Privacy_Bluetooth"),
        )
        ctk.CTkCheckBox(
            panel,
            text="Ho autorizzato il Bluetooth (se lo stato non è rilevabile automaticamente)",
            variable=bt_manual_confirm,
            command=refresh_states,
        ).pack(anchor=tk.W, padx=12, pady=(2, 12))

        footer = ctk.CTkFrame(dialog, fg_color="transparent")
        footer.pack(fill=tk.X, padx=16, pady=(0, 14))

        def finish_setup() -> None:
            screen_ok, access_ok, bt_ok = refresh_states()
            if not (screen_ok and access_ok and bt_ok):
                proceed = messagebox.askyesno(
                    "Permessi incompleti",
                    (
                        "Alcuni permessi risultano mancanti.\n"
                        "Se continui ora alcune funzioni (scan BLE/screenshot/shortcut) possono fallire.\n\n"
                        "Vuoi comunque chiudere il setup?"
                    ),
                    parent=dialog,
                )
                if not proceed:
                    return
            self._update_settings(
                {
                    "permissions_onboarding_done": True,
                    "permissions_screen_recording_ok": screen_ok,
                    "permissions_accessibility_ok": access_ok,
                    "permissions_bluetooth_ok": bt_ok,
                }
            )
            self._append_log("System", "Setup permessi macOS completato")
            dialog.destroy()
            self._permissions_dialog = None

        def remind_later() -> None:
            self._append_log("System", "Setup permessi rimandato")
            dialog.destroy()
            self._permissions_dialog = None

        ctk.CTkButton(
            footer,
            text="Ricarica stato",
            width=120,
            command=refresh_states,
            fg_color="transparent",
            border_width=1,
            hover_color="#2a2a2a",
        ).pack(side=tk.LEFT)
        ctk.CTkButton(
            footer,
            text="Ricorda dopo",
            width=120,
            command=remind_later,
            fg_color="transparent",
            border_width=1,
            hover_color="#2a2a2a",
        ).pack(side=tk.RIGHT, padx=(8, 0))
        ctk.CTkButton(footer, text="Completa setup", width=140, command=finish_setup).pack(side=tk.RIGHT, padx=(0, 8))

        dialog.protocol("WM_DELETE_WINDOW", remind_later)
        refresh_states()
        def poll_status() -> None:
            if self._permissions_dialog is None or not self._permissions_dialog.winfo_exists():
                return
            refresh_states()
            dialog.after(1800, poll_status)
        dialog.after(1800, poll_status)

    def _parse_version_tuple(self, value: str) -> tuple[int, ...]:
        clean = value.strip().lower()
        if clean.startswith("v"):
            clean = clean[1:]
        parts: list[int] = []
        for chunk in re.findall(r"\d+", clean):
            try:
                parts.append(int(chunk))
            except Exception:
                parts.append(0)
        return tuple(parts or [0])

    def _is_version_newer(self, candidate: str, current: str) -> bool:
        return self._parse_version_tuple(candidate) > self._parse_version_tuple(current)

    def _release_asset_for_platform(self, assets: list[dict[str, Any]]) -> dict[str, Any] | None:
        names = []
        if self._is_macos:
            names = ["BluetoothGeminiChat-macos.dmg", "BluetoothGeminiChat-macos.zip"]
        elif platform.system().lower().startswith("windows"):
            names = ["BluetoothGeminiChat-windows.zip"]
        for wanted in names:
            for asset in assets:
                if str(asset.get("name", "")) == wanted:
                    return asset
        return None

    def _download_update_asset(self, url: str, filename: str) -> Path:
        download_dir = Path.home() / "Downloads" / "GeminiBLEUpdates"
        download_dir.mkdir(parents=True, exist_ok=True)
        target = download_dir / filename
        payload = self._open_external_url(url, use_ssl_fallback=True)
        target.write_bytes(payload)
        return target

    def on_check_updates(self, background: bool = True) -> None:
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        try:
            raw = self._open_external_url(api_url, use_ssl_fallback=True)
            payload = json.loads(raw.decode("utf-8"))
        except (urlerror.URLError, TimeoutError, json.JSONDecodeError, ssl.SSLError) as exc:
            if not background:
                lowered = str(exc).lower()
                if "certificate_verify_failed" in lowered:
                    self._append_log(
                        "Error",
                        "Update check failed: certificati SSL non disponibili in questo ambiente.",
                    )
                else:
                    self._append_log("Error", f"Update check failed: {exc}")
            return

        latest_tag = str(payload.get("tag_name", "")).strip() or "unknown"
        release_url = str(payload.get("html_url", "")).strip()
        assets = payload.get("assets", [])
        if not isinstance(assets, list):
            assets = []
        if not self._is_version_newer(latest_tag, APP_VERSION):
            if not background:
                self._append_log("System", f"Already up to date ({APP_VERSION})")
            return

        self._append_log("System", f"Update available: {latest_tag} (current {APP_VERSION})")
        asset = self._release_asset_for_platform(assets)
        if asset is None:
            if release_url:
                self._append_log("System", f"Open release page: {release_url}")
            return

        asset_name = str(asset.get("name", "update.bin")).strip() or "update.bin"
        asset_url = str(asset.get("browser_download_url", "")).strip()
        if not asset_url:
            if release_url:
                self._append_log("System", f"Open release page: {release_url}")
            return

        if background:
            return

        from tkinter import messagebox
        do_download = messagebox.askyesno(
            "Update disponibile",
            f"Nuova versione {latest_tag} disponibile.\nVuoi scaricare {asset_name} adesso?",
            parent=self.root,
        )
        if not do_download:
            return

        try:
            local_path = self._download_update_asset(asset_url, asset_name)
        except Exception as exc:
            self._append_log("Error", f"Download update failed: {exc}")
            return

        self._append_log("System", f"Update downloaded: {local_path}")
        try:
            if self._is_macos and local_path.suffix.lower() == ".dmg":
                subprocess.Popen(["open", str(local_path)])
            elif platform.system().lower().startswith("windows"):
                os.startfile(str(local_path))  # type: ignore[attr-defined]
            else:
                webbrowser.open(local_path.as_uri(), new=2)
        except Exception as exc:
            self._append_log("System", f"Open update file manually: {local_path} ({exc})")

    def _track_pending_request(self, request_id: str, session_id: str) -> None:
        self._pending_request_session[request_id] = session_id
        self._pending_request_order = [rid for rid in self._pending_request_order if rid != request_id]
        self._pending_request_order.append(request_id)
        self._refresh_stop_button()

    def _clear_pending_request(self, request_id: str) -> None:
        self._pending_request_session.pop(request_id, None)
        if request_id in self._pending_request_order:
            self._pending_request_order = [rid for rid in self._pending_request_order if rid != request_id]
        self._refresh_stop_button()

    def _latest_pending_request_for_session(self, session_id: str) -> str | None:
        for request_id in reversed(self._pending_request_order):
            if self._pending_request_session.get(request_id) == session_id:
                return request_id
        return None

    def _refresh_stop_button(self) -> None:
        try:
            can_stop = self._latest_pending_request_for_session(self.active_session_id) is not None
            self.stop_btn.configure(state=("normal" if can_stop else "disabled"))
        except Exception:
            pass

    def _clear_all_pending_requests(self) -> None:
        for request_id in list(self._pending_request_order):
            self._clear_pending_request(request_id)

    def _maybe_auto_connect_on_start(self) -> None:
        if not self._auto_connect_on_start:
            return
        if self.connected:
            return
        if not self._last_connected_address:
            return
        self._append_log("System", f"Auto-connect to known bridge: {self._last_connected_address}")
        self.client.connect(self._last_connected_address)

    def _create_menu_bar_icon_image(self) -> Any | None:
        if PILImage is None or ImageDraw is None:
            return None
        try:
            size = 64
            icon = PILImage.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(icon)
            draw.rounded_rectangle((8, 8, size - 8, size - 8), radius=14, fill=(28, 28, 30, 255))
            draw.ellipse((18, 22, 34, 38), fill=(71, 160, 255, 255))
            draw.rectangle((32, 27, 46, 33), fill=(213, 213, 214, 255))
            return icon
        except Exception:
            return None

    def _start_menu_bar_icon_if_needed(self) -> None:
        if not self._menu_bar_mode_enabled:
            return
        if not self._menu_bar_available:
            if self._is_macos:
                self._append_log("System", "Menu bar mode unavailable: missing AppKit bridge")
            return

        if self._is_macos:
            self._start_macos_menu_bar_item()
            return

        if self._tray_icon is not None:
            return

        image = self._create_menu_bar_icon_image()
        if image is None:
            return

        def on_toggle(_icon: Any, _item: Any) -> None:
            self.root.after(0, self._toggle_app_visibility)

        def on_shot(_icon: Any, _item: Any) -> None:
            self.root.after(0, self.on_hotkey_overlay_triggered)

        def on_clip(_icon: Any, _item: Any) -> None:
            self.root.after(0, self.on_hotkey_clipboard_triggered)

        def on_reconnect(_icon: Any, _item: Any) -> None:
            self.root.after(0, self._reconnect_last_or_selected)

        def on_quit(_icon: Any, _item: Any) -> None:
            self.root.after(0, self.on_close)

        menu = pystray.Menu(
            pystray.MenuItem("Show/Hide Window", on_toggle),
            pystray.MenuItem("Shot+Ask", on_shot),
            pystray.MenuItem("Clipboard+Ask", on_clip),
            pystray.MenuItem("Reconnect", on_reconnect),
            pystray.MenuItem("Quit", on_quit),
        )
        icon = pystray.Icon("gemini_ble_chat", image, "Gemini BLE Chat", menu)
        self._tray_icon = icon

        def run_icon() -> None:
            try:
                icon.run()
            except Exception:
                pass

        self._tray_thread = threading.Thread(target=run_icon, daemon=True)
        self._tray_thread.start()

    def _start_macos_menu_bar_item(self) -> None:
        if not self._is_macos:
            return
        if self._mac_status_item is not None:
            return
        if NSStatusBar is None or NSMenu is None or NSMenuItem is None or _MacMenuActionTarget is None:
            self._append_log("System", "Menu bar mode unavailable on this build")
            return

        status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        if status_item is None:
            self._append_log("System", "Cannot create macOS status item")
            return

        button = status_item.button()
        if button is not None:
            if NSImage is not None:
                try:
                    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                        "bubble.left.and.bubble.right.fill",
                        "Gemini BLE Chat",
                    )
                    if image is not None:
                        button.setImage_(image)
                    else:
                        button.setTitle_("G")
                except Exception:
                    button.setTitle_("G")
            else:
                button.setTitle_("G")

        menu = NSMenu.alloc().init()
        targets: list[Any] = []

        def add_item(title: str, callback: Any) -> None:
            target = _MacMenuActionTarget.alloc().initWithCallback_(callback)
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "onAction:", "")
            item.setTarget_(target)
            menu.addItem_(item)
            targets.append(target)

        add_item("Show/Hide Window", lambda: self.root.after(0, self._toggle_app_visibility))
        add_item("Shot+Ask", lambda: self.root.after(0, self.on_hotkey_overlay_triggered))
        add_item("Clipboard+Ask", lambda: self.root.after(0, self.on_hotkey_clipboard_triggered))
        add_item("Reconnect", lambda: self.root.after(0, self._reconnect_last_or_selected))
        menu.addItem_(NSMenuItem.separatorItem())
        add_item("Quit", lambda: self.root.after(0, self.on_close))

        status_item.setMenu_(menu)
        self._mac_status_item = status_item
        self._mac_status_menu = menu
        self._mac_status_targets = targets

    def _stop_macos_menu_bar_item(self) -> None:
        item = self._mac_status_item
        self._mac_status_item = None
        self._mac_status_menu = None
        self._mac_status_targets = []
        if item is None:
            return
        try:
            NSStatusBar.systemStatusBar().removeStatusItem_(item)
        except Exception:
            pass

    def _stop_menu_bar_icon(self) -> None:
        if self._is_macos:
            self._stop_macos_menu_bar_item()
            return
        icon = self._tray_icon
        self._tray_icon = None
        if icon is None:
            return
        try:
            icon.stop()
        except Exception:
            pass

    def _apply_macos_activation_policy(self) -> None:
        if not self._is_macos:
            return
        if NSApplication is None:
            if not self._macos_policy_applied:
                self._append_log("System", "Dock policy control unavailable (missing AppKit bridge)")
            self._macos_policy_applied = True
            return
        try:
            app = NSApplication.sharedApplication()
            if self._hide_dock_icon_enabled:
                app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            else:
                app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
            self._macos_policy_applied = True
        except Exception as exc:
            if not self._macos_policy_applied:
                self._append_log("System", f"Dock policy update failed: {exc}")
            self._macos_policy_applied = True

    def _reconnect_last_or_selected(self) -> None:
        if self.connected:
            self.client.disconnect()
        selected = self.devices_list.curselection()
        if selected:
            idx = selected[0]
            if 0 <= idx < len(self.devices):
                self.client.connect(self.devices[idx]["address"])
                return
        if self._last_connected_address:
            self.client.connect(self._last_connected_address)

    def on_open_settings(self) -> None:
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Settings")
        dialog.geometry("620x760")
        dialog.minsize(540, 520)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        content = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        content.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)

        # --- Section 1: System Instructions ---
        ctk.CTkLabel(content, text="System Instructions (Global Prompt):", font=("Avenir", 14, "bold")).pack(pady=(12, 4), padx=12, anchor=tk.W)
        ctk.CTkLabel(content, text="Iniettate silenziosamente in ogni richiesta.", font=("Avenir", 11), text_color="#888888").pack(padx=12, anchor=tk.W)

        textbox = ctk.CTkTextbox(content, height=120, font=("Avenir", 13), fg_color="#1e1e1e", border_width=1, border_color="#333333")
        textbox.pack(fill=tk.X, padx=12, pady=(6, 12))
        textbox.insert("1.0", self.system_instructions_var.get())

        # --- Section 2: Pinned PDFs ---
        ctk.CTkLabel(content, text="📚 Documenti Fissi (PDF sempre attivi):", font=("Avenir", 14, "bold")).pack(pady=(4, 4), padx=12, anchor=tk.W)
        ctk.CTkLabel(content, text="Allegati automaticamente ad ogni messaggio senza doverli ricaricare.", font=("Avenir", 11), text_color="#888888").pack(padx=12, anchor=tk.W)

        pinned_list_var = tk.Variable(value=list(self.pinned_pdf_paths))
        pdf_listbox = tk.Listbox(
            content,
            listvariable=pinned_list_var,
            height=5,
            bg="#1e1e1e",
            fg="#dddddd",
            selectbackground="#1f538d",
            borderwidth=0,
            highlightthickness=0,
            font=("Avenir", 12),
        )
        pdf_listbox.pack(fill=tk.X, padx=12, pady=(6, 4))

        def add_pdf() -> None:
            from tkinter import filedialog
            paths = filedialog.askopenfilenames(
                parent=dialog, title="Seleziona PDF",
                filetypes=[("PDF", "*.pdf"), ("All Files", "*")],
            )
            current = list(pinned_list_var.get())
            for p in paths:
                if p not in current:
                    current.append(p)
            pinned_list_var.set(current)

        def remove_pdf() -> None:
            idxs = pdf_listbox.curselection()
            current = list(pinned_list_var.get())
            for i in reversed(idxs):
                del current[i]
            pinned_list_var.set(current)

        pdf_btn_row = ctk.CTkFrame(content, fg_color="transparent")
        pdf_btn_row.pack(fill=tk.X, padx=12, pady=(0, 12))
        ctk.CTkButton(pdf_btn_row, text="+ Aggiungi PDF", command=add_pdf, width=120, fg_color="#1f538d").pack(side=tk.LEFT, padx=(0, 8))
        ctk.CTkButton(pdf_btn_row, text="− Rimuovi selezionato", command=remove_pdf, width=160, fg_color="transparent", border_width=1, hover_color="#333333").pack(side=tk.LEFT)

        # --- Section 3: Shot+Ask Overlay ---
        ctk.CTkLabel(content, text="🪟 Shot+Ask Overlay:", font=("Avenir", 14, "bold")).pack(pady=(4, 4), padx=12, anchor=tk.W)
        ctk.CTkLabel(
            content,
            text="Scegli sfondo e dimensioni della finestra risposta.",
            font=("Avenir", 11),
            text_color="#888888",
        ).pack(padx=12, anchor=tk.W)

        overlay_bg_var = tk.StringVar(value=self._overlay_bg_color)
        overlay_width_var = tk.StringVar(value=str(self._overlay_width))
        overlay_height_var = tk.StringVar(value=str(self._overlay_height))
        overlay_resizable_var = tk.BooleanVar(value=self._overlay_resizable)

        overlay_bg_row = ctk.CTkFrame(content, fg_color="transparent")
        overlay_bg_row.pack(fill=tk.X, padx=12, pady=(6, 6))
        ctk.CTkLabel(overlay_bg_row, text="Sfondo (#RRGGBB):", width=120).pack(side=tk.LEFT)
        ctk.CTkEntry(overlay_bg_row, textvariable=overlay_bg_var, width=120).pack(side=tk.LEFT, padx=(0, 8))
        color_swatch = tk.Frame(
            overlay_bg_row,
            width=24,
            height=24,
            bg=self._overlay_bg_color,
            highlightthickness=1,
            highlightbackground="#555555",
        )
        color_swatch.pack(side=tk.LEFT, padx=(0, 8))
        color_swatch.pack_propagate(False)

        def choose_overlay_bg() -> None:
            _, picked = colorchooser.askcolor(color=overlay_bg_var.get().strip(), parent=dialog, title="Sfondo overlay")
            if not picked:
                return
            overlay_bg_var.set(picked)
            color_swatch.configure(bg=picked)

        ctk.CTkButton(overlay_bg_row, text="Scegli", width=80, command=choose_overlay_bg).pack(side=tk.LEFT)

        def on_overlay_bg_changed(*_: Any) -> None:
            value = self._normalize_hex_color(overlay_bg_var.get(), "")
            if value:
                color_swatch.configure(bg=value)

        overlay_bg_var.trace_add("write", on_overlay_bg_changed)

        overlay_size_row = ctk.CTkFrame(content, fg_color="transparent")
        overlay_size_row.pack(fill=tk.X, padx=12, pady=(0, 8))
        ctk.CTkLabel(overlay_size_row, text="Larghezza:", width=120).pack(side=tk.LEFT)
        ctk.CTkEntry(overlay_size_row, textvariable=overlay_width_var, width=80).pack(side=tk.LEFT, padx=(0, 12))
        ctk.CTkLabel(overlay_size_row, text="Altezza:", width=70).pack(side=tk.LEFT)
        ctk.CTkEntry(overlay_size_row, textvariable=overlay_height_var, width=80).pack(side=tk.LEFT)

        ctk.CTkCheckBox(
            content,
            text="Consenti ridimensionamento manuale finestra overlay",
            variable=overlay_resizable_var,
        ).pack(anchor=tk.W, padx=12, pady=(0, 12))

        # --- Section 4: Connection & macOS Shell ---
        ctk.CTkLabel(content, text="🔗 Connessione:", font=("Avenir", 14, "bold")).pack(pady=(4, 4), padx=12, anchor=tk.W)
        auto_connect_var = tk.BooleanVar(value=self._auto_connect_on_start)
        auto_retry_var = tk.BooleanVar(value=self._auto_retry_known_device)
        auto_updates_var = tk.BooleanVar(value=self._auto_check_updates)
        close_bg_var = tk.BooleanVar(value=self._close_to_background_on_close)
        ctk.CTkCheckBox(
            content,
            text="Auto-connect all'avvio (ultimo telefono noto)",
            variable=auto_connect_var,
        ).pack(anchor=tk.W, padx=12, pady=(0, 4))
        ctk.CTkCheckBox(
            content,
            text="Auto-retry su disconnessione (backoff)",
            variable=auto_retry_var,
        ).pack(anchor=tk.W, padx=12, pady=(0, 12))
        ctk.CTkCheckBox(
            content,
            text="Controlla aggiornamenti automaticamente",
            variable=auto_updates_var,
        ).pack(anchor=tk.W, padx=12, pady=(0, 12))
        ctk.CTkCheckBox(
            content,
            text="Con la X chiudi solo la finestra (app resta in background)",
            variable=close_bg_var,
        ).pack(anchor=tk.W, padx=12, pady=(0, 12))

        ctk.CTkLabel(content, text="🍎 macOS Shell:", font=("Avenir", 14, "bold")).pack(pady=(4, 4), padx=12, anchor=tk.W)
        menu_bar_mode_var = tk.BooleanVar(value=self._menu_bar_mode_enabled)
        hide_dock_var = tk.BooleanVar(value=self._hide_dock_icon_enabled)
        ctk.CTkCheckBox(
            content,
            text="Mostra icona nella barra in alto (menu bar)",
            variable=menu_bar_mode_var,
        ).pack(anchor=tk.W, padx=12, pady=(0, 4))
        ctk.CTkCheckBox(
            content,
            text="Nascondi icona nella Dock",
            variable=hide_dock_var,
        ).pack(anchor=tk.W, padx=12, pady=(0, 12))
        if self._is_macos:
            ctk.CTkButton(
                content,
                text="Configura permessi macOS",
                command=lambda: self._show_permissions_onboarding(force=True),
                fg_color="transparent",
                border_width=1,
                hover_color="#333333",
            ).pack(anchor=tk.W, padx=12, pady=(0, 12))

        def save() -> None:
            text = textbox.get("1.0", tk.END).strip()
            self.system_instructions_var.set(text)
            self.pinned_pdf_paths = list(pinned_list_var.get())
            self._overlay_bg_color = self._normalize_hex_color(overlay_bg_var.get(), self._overlay_bg_color)
            self._overlay_width = self._parse_int_setting(overlay_width_var.get(), self._overlay_width, 320, 1280)
            self._overlay_height = self._parse_int_setting(overlay_height_var.get(), self._overlay_height, 160, 900)
            self._overlay_resizable = bool(overlay_resizable_var.get())
            self._auto_connect_on_start = bool(auto_connect_var.get())
            self._auto_retry_known_device = bool(auto_retry_var.get())
            self._auto_check_updates = bool(auto_updates_var.get())
            self._close_to_background_on_close = bool(close_bg_var.get())
            self._menu_bar_mode_enabled = bool(menu_bar_mode_var.get())
            self._hide_dock_icon_enabled = bool(hide_dock_var.get())
            self.client.set_auto_reconnect(self._auto_retry_known_device)
            old_settings = self._load_settings()
            old_settings["system_instructions"] = text
            old_settings["pinned_pdf_paths"] = self.pinned_pdf_paths
            old_settings["overlay_bg_color"] = self._overlay_bg_color
            old_settings["overlay_width"] = self._overlay_width
            old_settings["overlay_height"] = self._overlay_height
            old_settings["overlay_resizable"] = self._overlay_resizable
            old_settings["auto_connect_on_start"] = self._auto_connect_on_start
            old_settings["auto_retry_known_device"] = self._auto_retry_known_device
            old_settings["auto_check_updates"] = self._auto_check_updates
            old_settings["close_to_background_on_close"] = self._close_to_background_on_close
            old_settings["menu_bar_mode_enabled"] = self._menu_bar_mode_enabled
            old_settings["hide_dock_icon_enabled"] = self._hide_dock_icon_enabled
            old_settings["last_connected_address"] = self._last_connected_address
            self._save_settings(old_settings)
            self._apply_overlay_window_preferences()
            if self._menu_bar_mode_enabled:
                self._start_menu_bar_icon_if_needed()
            else:
                self._stop_menu_bar_icon()
            self._apply_macos_activation_policy()
            dialog.destroy()
            n = len(self.pinned_pdf_paths)
            self._append_log(
                "System",
                (
                    f"Settings saved. Pinned PDFs: {n}. "
                    f"System instructions: {'YES' if text else 'none'}. "
                    f"Overlay: {self._overlay_width}x{self._overlay_height}, bg {self._overlay_bg_color}. "
                    f"Auto-connect: {'on' if self._auto_connect_on_start else 'off'}. "
                    f"Auto-retry: {'on' if self._auto_retry_known_device else 'off'}. "
                    f"Close button to background: {'on' if self._close_to_background_on_close else 'off'}."
                ),
            )

        ctk.CTkButton(content, text="💾 SALVA", command=save, fg_color="#1f538d").pack(pady=(4, 16), padx=12, anchor=tk.E)

    # ── Knowledge Base Container handlers ─────────────────────────────────────

    def _refresh_container_list(self) -> None:
        self.container_list.delete(0, tk.END)
        containers = self._context_store.all()
        for c in containers:
            active_mark = "✓ " if c.id == self._active_container_id else "   "
            label = f"{active_mark}{c.name}  ({c.total_chunks()} chunk)"
            self.container_list.insert(tk.END, label)

        # Restore the highlighted selection
        if self._selected_container_idx is not None and self._selected_container_idx < len(containers):
            self.container_list.selection_set(self._selected_container_idx)
            self.container_list.see(self._selected_container_idx)

        # Update the active indicator label
        active = next((c for c in containers if c.id == self._active_container_id), None)
        if active:
            self._kb_active_label.configure(
                text=f"● {active.name} attivo", text_color="#4caf50"
            )
        else:
            remote_active = next(
                (c for c in self._remote_containers if str(c.get("id", "")) == str(self._active_container_id)),
                None,
            )
            if remote_active is not None:
                self._kb_active_label.configure(
                    text=f"● [Remote] {remote_active.get('name', 'container')} attivo",
                    text_color="#4caf50",
                )
            else:
                self._kb_active_label.configure(text="nessun container attivo", text_color="#555555")

    def _active_container_name(self) -> str | None:
        if not self._active_container_id:
            return None
        local = self._context_store.get(self._active_container_id)
        if local is not None:
            return local.name
        remote = next(
            (c for c in self._remote_containers if str(c.get("id", "")) == str(self._active_container_id)),
            None,
        )
        if remote is not None:
            name = str(remote.get("name", "")).strip()
            if name:
                return name
        return None

    def _selected_container_id(self) -> str | None:
        """Return the ID of the currently highlighted container (survives refresh)."""
        # Prefer the tracked index (survives _refresh_container_list)
        idx = self._selected_container_idx
        if idx is None:
            sel = self.container_list.curselection()
            if sel:
                idx = sel[0]
        if idx is None:
            return None
        containers = self._context_store.all()
        if idx >= len(containers):
            return None
        return containers[idx].id

    def _on_create_container(self) -> None:
        name = simpledialog.askstring("Nuovo Container", "Nome della libreria:", parent=self.root)
        if not name or not name.strip():
            return
        c = self._context_store.create(name.strip())
        self._append_log("System", f"Container creato: '{c.name}' (id: {c.id[:8]})")
        self._refresh_container_list()

    def _on_container_list_click(self, _event=None) -> None:
        """User clicked a container row — update the highlighted index."""
        sel = self.container_list.curselection()
        if not sel:
            return
        self._selected_container_idx = sel[0]
        # Don't set active yet — user uses the ✓ toggle via double-click or separate activate button.
        # Just refresh labels so the selection is visible.
        self._refresh_container_list()

    def _on_activate_container(self, _event=None) -> None:
        """Double-click on container row → activate/deactivate it."""
        cid = self._selected_container_id()
        if cid is None:
            return
        if self._active_container_id == cid:
            self._active_container_id = None
            self._append_log("System", "Container deattivato — contesto PDF disabilitato")
        else:
            self._active_container_id = cid
            c = self._context_store.get(cid)
            if c:
                self._append_log("System", f"Container attivo: '{c.name}' ({c.total_chunks()} chunk)")
        self._refresh_container_list()

    def _on_add_pdf_to_container(self) -> None:
        cid = self._selected_container_id()
        if cid is None:
            self._append_log("System", "Seleziona prima un container dalla lista 📚")
            return
        paths = filedialog.askopenfilenames(
            parent=self.root, title="Aggiungi PDF al container",
            filetypes=[("PDF", "*.pdf"), ("All Files", "*")],
        )
        if not paths:
            return
        c = self._context_store.get(cid)
        for p in paths:
            try:
                n = self._context_store.add_pdf(cid, p)
                self._append_log("System", f"PDF aggiunto: {Path(p).name} → {n} chunk estratti")
            except ValueError as exc:
                self._append_log("Error", str(exc))
        self._refresh_container_list()

    def _on_delete_container(self) -> None:
        from tkinter import messagebox
        cid = self._selected_container_id()
        if cid is None:
            return
        c = self._context_store.get(cid)
        if not messagebox.askyesno("Elimina Container", f"Eliminare '{c.name if c else cid}'?", parent=self.root):
            return
        self._context_store.delete(cid)
        if self._active_container_id == cid:
            self._active_container_id = None
        self._refresh_container_list()
        self._append_log("System", "Container eliminato")

    def _on_upload_container(self) -> None:
        cid = self._selected_container_id()
        if cid is None:
            self._append_log("System", "Seleziona un container da caricare sul telefono")
            return
        if not self.connected:
            self._append_log("System", "Non connesso — connetti il bridge Android prima di caricare")
            return
        c = self._context_store.get(cid)
        if c is None or c.total_chunks() == 0:
            self._append_log("System", "Container vuoto — aggiungi prima dei PDF")
            return
        try:
            container_dict = self._context_store.export_for_transfer(cid)
            payload_bytes = len(str(container_dict).encode("utf-8"))
            size_kb = payload_bytes // 1024
            self._append_log("System", f"Avvio trasferimento container '{c.name}' (~{size_kb}KB, {c.total_chunks()} chunk)...")
            request_id = self.client.send_container(container_dict)
            self._container_transfer_request_id = request_id
            self._container_transfer_container_id_by_request[request_id] = cid
            self._open_transfer_dialog(c.name, size_kb)
        except ValueError as exc:
            self._append_log("Error", str(exc))

    def _on_sync_remote_containers(self) -> None:
        if not self.connected:
            self._append_log("System", "Non connesso — connetti il bridge Android prima di sincronizzare")
            return
        try:
            request_id = self.client.request_container_list()
            self._append_log("System", f"Richiesta lista container remoti ({request_id})")
        except Exception as exc:
            self._append_log("Error", f"Sync container remoti fallita: {exc}")

    def _open_remote_container_picker(self) -> None:
        if not self._remote_containers:
            self._append_log("System", "Nessun container remoto disponibile")
            return

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Container remoti")
        dialog.geometry("460x340")
        dialog.transient(self.root)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Container disponibili sul telefono", font=("Avenir", 14, "bold")).pack(
            anchor=tk.W, padx=12, pady=(10, 8)
        )
        lb = tk.Listbox(
            dialog,
            bg="#1e1e1e",
            fg="#e0e0e0",
            selectbackground="#1f538d",
            activestyle=tk.NONE,
            borderwidth=1,
            relief=tk.SOLID,
            highlightthickness=0,
        )
        lb.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))
        for c in self._remote_containers:
            name = str(c.get("name", "container")).strip() or "container"
            chunks = int(c.get("chunkCount", 0) or 0)
            lb.insert(tk.END, f"{name} ({chunks} chunk)")

        if self._active_container_id:
            for idx, c in enumerate(self._remote_containers):
                if str(c.get("id", "")) == self._active_container_id:
                    lb.selection_set(idx)
                    lb.see(idx)
                    break

        def activate_selected() -> None:
            sel = lb.curselection()
            if not sel:
                return
            chosen = self._remote_containers[sel[0]]
            cid = str(chosen.get("id", "")).strip()
            if not cid:
                return
            self._active_container_id = cid
            self._refresh_container_list()
            self._refresh_context_preview()
            self._append_log(
                "System",
                f"Container remoto attivo: {chosen.get('name', 'container')} ({int(chosen.get('chunkCount', 0) or 0)} chunk)",
            )
            dialog.destroy()

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(fill=tk.X, padx=12, pady=(0, 12))
        ctk.CTkButton(btn_row, text="Attiva", command=activate_selected, width=100).pack(side=tk.LEFT)
        ctk.CTkButton(btn_row, text="Chiudi", command=dialog.destroy, width=100, fg_color="transparent", border_width=1).pack(
            side=tk.LEFT, padx=(8, 0)
        )

    # ── Transfer progress dialog ──────────────────────────────────────────────

    def _open_transfer_dialog(self, container_name: str, size_kb: int) -> None:
        import time as _time
        self._transfer_started_time = _time.monotonic()

        d = ctk.CTkToplevel(self.root)
        d.title("Trasferimento BLE")
        d.geometry("400x180")
        d.resizable(False, False)
        d.grab_set()
        d.attributes("-topmost", True)
        self._transfer_dialog = d

        ctk.CTkLabel(d, text=f"📡 Caricamento libreria sul telefono", font=("Avenir", 14, "bold")).pack(pady=(18, 4))
        ctk.CTkLabel(d, text=container_name, font=("Avenir", 12), text_color="#888888").pack()

        self._transfer_progress_var = tk.DoubleVar(value=0.0)
        bar = ctk.CTkProgressBar(d, variable=self._transfer_progress_var, width=340, height=14)
        bar.pack(pady=(14, 6))

        self._transfer_label_var = tk.StringVar(value=f"0%  —  0 / ? pacchetti  (~{size_kb} KB)")
        ctk.CTkLabel(d, textvariable=self._transfer_label_var, font=("Avenir", 11), text_color="#aaaaaa").pack()

    def _update_transfer_dialog(self, percent: int, current: int, total: int) -> None:
        import time as _time
        if self._transfer_progress_var is None or self._transfer_label_var is None:
            return
        self._transfer_progress_var.set(percent / 100.0)
        elapsed = _time.monotonic() - self._transfer_started_time
        if current > 0:
            eta_sec = (elapsed / current) * (total - current)
            eta_str = f"  —  ETA {eta_sec:.0f}s" if eta_sec > 1 else ""
        else:
            eta_str = ""
        self._transfer_label_var.set(f"{percent}%  —  {current}/{total} pacchetti{eta_str}")
        if percent >= 100:
            self._transfer_label_var.set("100%  —  pacchetti inviati, attendo conferma telefono…")

    def _close_transfer_dialog(self, success: bool = True) -> None:
        d = self._transfer_dialog
        request_id = self._container_transfer_request_id
        if d is None:
            return
        self._transfer_dialog = None
        self._container_transfer_request_id = None
        try:
            d.grab_release()
            d.destroy()
        except Exception:
            pass
        if success:
            self._append_log("System", "➜ Pacchetti inviati. In attesa di conferma di salvataggio dal telefono...")
            # Auto-activate the container that was just uploaded.
            cid = self._container_transfer_container_id_by_request.pop(request_id or "", None) or self._selected_container_id()
            if cid:
                self._active_container_id = cid
                c = self._context_store.get(cid)
                self._append_log("System", f"Container auto-attivato: {c.name if c else cid}")
                self._refresh_container_list()
                self._refresh_context_preview()
        else:
            if request_id:
                self._container_transfer_container_id_by_request.pop(request_id, None)


    def _toggle_pip(self) -> None:

        if self.pip_enabled.get():
            self._pip_mode_active = True
            self._pre_pip_geometry = self.root.geometry()
            self.root.attributes('-topmost', True)
            self.root.geometry("400x500")
        else:
            self._pip_mode_active = False
            self.root.attributes('-topmost', False)
            if self._pre_pip_geometry:
                self.root.geometry(self._pre_pip_geometry)

    def _on_window_resize(self, event) -> None:
        if event.widget != self.root:
            return
        width = event.width
        # Threshold for compact mode
        if width < 750 and not self._is_compact_mode:
            self._is_compact_mode = True
            self.header_frame.grid_remove()
            self.sidebar_frame.grid_remove()
            self.chat_area_frame.grid(row=0, column=0, rowspan=2, columnspan=2, sticky="nsew", padx=0, pady=0)
            # Remove padding for pure chat view
            self.root.configure(padx=0, pady=0)
        elif width >= 750 and self._is_compact_mode:
            self._is_compact_mode = False
            self.header_frame.grid()
            self.sidebar_frame.grid()
            self.chat_area_frame.grid(row=1, column=1, rowspan=1, columnspan=1, sticky="nsew", padx=(6, 12), pady=(0, 12))

    def _set_phone_default_model(self) -> None:
        self.model_var.set(MODEL_PRESETS[0])
        self._refresh_context_preview()

    def _build_button_grid(
        self,
        parent: tk.Widget,
        entries: list[tuple[str, Any]],
        columns: int = 2,
        pady: tuple[int, int] = (6, 6),
    ) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent)
        frame.pack(fill=tk.X, pady=pady)
        for col in range(columns):
            frame.columnconfigure(col, weight=1, uniform=f"btn-{id(frame)}")

        for idx, (label, command) in enumerate(entries):
            row = idx // columns
            col = idx % columns
            pad_right = 6 if col < (columns - 1) else 0
            pad_bottom = 6 if idx < (len(entries) - columns) else 0
            ctk.CTkButton(frame, text=label, command=command, fg_color="transparent", border_width=1, hover_color="#333333", text_color="#e0e0e0").grid(
                row=row,
                column=col,
                sticky="ew",
                padx=(0, pad_right),
                pady=(0, pad_bottom),
            )

        return frame

    def _configure_chat_tags(self) -> None:
        self.chat_log._textbox.tag_configure("role_you", foreground="#6ba5ff", font=("Avenir", 10, "bold"), spacing1=10)
        self.chat_log._textbox.tag_configure(
            "msg_you",
            foreground="#e0e0e0",
            background="#1a2b42",
            lmargin1=200,
            lmargin2=200,
            rmargin=14,
            spacing3=8,
        )

        self.chat_log._textbox.tag_configure("role_gemini", foreground="#4de68d", font=("Avenir", 10, "bold"), spacing1=10)
        self.chat_log._textbox.tag_configure(
            "msg_gemini",
            foreground="#e0e0e0",
            background="#14261d",
            lmargin1=14,
            lmargin2=14,
            rmargin=210,
            spacing3=8,
        )

        self.chat_log._textbox.tag_configure("role_thought", foreground="#e5b229", font=("Avenir", 10, "bold"), spacing1=8)
        self.chat_log._textbox.tag_configure(
            "msg_thought",
            foreground="#d1a634",
            background="#2b220d",
            lmargin1=14,
            lmargin2=14,
            rmargin=210,
            spacing3=8,
        )

        self.chat_log._textbox.tag_configure("role_phone", foreground="#9e8dff", font=("Avenir", 10, "bold"), spacing1=8)
        self.chat_log._textbox.tag_configure("msg_phone", foreground="#a1a1a1", lmargin1=14, lmargin2=14, rmargin=160)

        self.chat_log._textbox.tag_configure("role_system", foreground="#a9b9cf", font=("Avenir", 10, "bold"), spacing1=8)
        self.chat_log._textbox.tag_configure("msg_system", foreground="#a9b9cf", lmargin1=14, lmargin2=14, rmargin=100)

        self.chat_log._textbox.tag_configure("role_error", foreground="#ff6b6b", font=("Avenir", 10, "bold"), spacing1=8)
        self.chat_log._textbox.tag_configure("msg_error", foreground="#ff6b6b", lmargin1=14, lmargin2=14, rmargin=100)
        self.chat_log._textbox.tag_configure("md_h1", font=("Avenir", 14, "bold"), spacing1=10)
        self.chat_log._textbox.tag_configure("md_h2", font=("Avenir", 13, "bold"), spacing1=8)
        self.chat_log._textbox.tag_configure("md_h3", font=("Avenir", 12, "bold"), spacing1=6)
        self.chat_log._textbox.tag_configure("md_bold", font=("Avenir", 11, "bold"))
        self.chat_log._textbox.tag_configure("md_inline_code", font=("Menlo", 10), background="#2a2a2a", foreground="#a6c8ff")
        self.chat_log._textbox.tag_configure("md_code_block", font=("Menlo", 10), background="#f7f9fc", foreground="#e0e0e0")
        self.chat_log._textbox.tag_configure("md_link", foreground="#5c9dff", underline=True)

    def _on_prompt_return(self, event: tk.Event[tk.Text]) -> str | None:
        # Shift+Enter inserts newline; Enter sends.
        if event.state & 0x0001:
            return None
        self.on_send()
        return "break"

    def _on_clear_composer_hotkey(self, _: tk.Event[tk.Text]) -> str:
        self.prompt_entry.delete("1.0", tk.END)
        return "break"

    def _start_overlay_hotkey_listener(self) -> None:
        if self._is_macos:
            self._append_log(
                "System",
                "Overlay trigger su macOS via Apple Shortcuts: ~/.gemini_ble/ask_gemini_ble_shot.sh",
            )
            return

        if pynput_keyboard is None:
            self._append_log("System", "Global hotkey disabled: install 'pynput' to enable overlay shortcut")
            return

        combo = "<ctrl>+<shift>+g"
        try:
            listener = pynput_keyboard.GlobalHotKeys(
                {
                    combo: lambda: self.events.put({"type": "hotkey_overlay"}),
                }
            )
            listener.start()
            self._overlay_listener = listener
            self._append_log("System", f"Global hotkey ready: {self._overlay_hotkey}")
        except Exception as exc:
            self._append_log("Error", f"Global hotkey unavailable: {exc}")

    def _apply_overlay_window_preferences(self) -> None:
        win = self._overlay_window
        if win is None or not win.winfo_exists():
            return
        try:
            win.configure(bg=self._overlay_bg_color)
            win.resizable(self._overlay_resizable, self._overlay_resizable)
            win.geometry(f"{self._overlay_width}x{self._overlay_height}")
        except Exception:
            pass
        if self._overlay_message_widget is not None:
            try:
                self._overlay_message_widget.configure(bg=self._overlay_bg_color, width=max(140, self._overlay_width - 30))
            except Exception:
                pass

    def _present_overlay_window(self, force: bool = False) -> None:
        win = self._overlay_window
        if win is None or not win.winfo_exists():
            return
        now = time.monotonic()
        if not force and (now - self._overlay_last_present_at) < 0.7:
            return
        self._overlay_last_present_at = now
        try:
            win.deiconify()
        except Exception:
            pass
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        if self._is_macos:
            try:
                self.root.tk.call("::tk::unsupported::MacWindowStyle", "style", win._w, "floating", "none")
            except Exception:
                pass
        try:
            win.lift()
        except Exception:
            pass

    def _on_overlay_window_configure(self, event: tk.Event[Any]) -> None:
        if self._overlay_window is None or event.widget is not self._overlay_window:
            return
        width = max(320, int(getattr(event, "width", self._overlay_width)))
        height = max(160, int(getattr(event, "height", self._overlay_height)))
        self._overlay_width = width
        self._overlay_height = height
        if self._overlay_message_widget is not None:
            try:
                self._overlay_message_widget.configure(width=max(140, width - 30))
            except Exception:
                pass

    def _show_overlay_message(self, text: str, ttl_ms: int = 12000) -> None:
        clean = text.strip()
        if not clean:
            return

        if self._overlay_window is None or not self._overlay_window.winfo_exists():
            win = tk.Toplevel(self.root)
            win.attributes("-topmost", True)
            try:
                win.attributes("-alpha", 0.5)
            except Exception:
                pass
            win.title("Gemini Quick Reply")
            win.configure(bg=self._overlay_bg_color)
            win.resizable(self._overlay_resizable, self._overlay_resizable)

            width, height = self._overlay_width, self._overlay_height
            x = max(12, win.winfo_screenwidth() - width - 18)
            y = max(12, win.winfo_screenheight() - height - 40)
            win.geometry(f"{width}x{height}+{x}+{y}")
            win.bind("<Configure>", self._on_overlay_window_configure)

            frame = tk.Frame(win, bg=self._overlay_bg_color, padx=12, pady=10)
            frame.pack(fill=tk.BOTH, expand=True)
            tk.Label(
                frame,
                text="Gemini Quick Reply",
                bg=self._overlay_bg_color,
                fg="#dbeafe",
                font=("Avenir", 11, "bold"),
                anchor="w",
            ).pack(fill=tk.X)
            message_widget = tk.Message(
                frame,
                textvariable=self._overlay_text_var,
                bg=self._overlay_bg_color,
                fg="#f8fafc",
                font=("Avenir", 11),
                width=max(140, width - 30),
                anchor="w",
                justify=tk.LEFT,
            )
            message_widget.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

            self._overlay_window = win
            self._overlay_message_widget = message_widget
        else:
            self._apply_overlay_window_preferences()

        self._overlay_text_var.set(clean[:1800])
        self._present_overlay_window(force=True)
        if self._overlay_hide_after_id is not None:
            self.root.after_cancel(self._overlay_hide_after_id)
            self._overlay_hide_after_id = None
        if ttl_ms > 0:
            self._overlay_hide_after_id = self.root.after(ttl_ms, self._hide_overlay_window)

    def _hide_overlay_window(self) -> None:
        if self._overlay_hide_after_id is not None:
            try:
                self.root.after_cancel(self._overlay_hide_after_id)
            except Exception:
                pass
            self._overlay_hide_after_id = None
        win = self._overlay_window
        self._overlay_window = None
        self._overlay_message_widget = None
        if win is not None and win.winfo_exists():
            win.destroy()

    def _cleanup_overlay_request(self, request_id: str) -> None:
        self._overlay_request_ids.discard(request_id)
        self._overlay_started_at.pop(request_id, None)
        self._overlay_last_update_at.pop(request_id, None)
        self._clear_pending_request(request_id)
        path = self._overlay_image_paths_by_request.pop(request_id, None)
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

    def _cleanup_all_overlay_requests(self) -> None:
        for request_id in list(self._overlay_request_ids):
            self._cleanup_overlay_request(request_id)

    def _check_overlay_request_timeouts(self) -> None:
        if not self._overlay_request_ids:
            return
        now = time.monotonic()
        timed_out: list[str] = []
        for request_id in list(self._overlay_request_ids):
            started_at = self._overlay_started_at.get(request_id, now)
            if (now - started_at) >= self._overlay_timeout_seconds:
                timed_out.append(request_id)
        for request_id in timed_out:
            self._show_overlay_message(
                "Timeout Shot+Ask: nessuna risposta dal bridge. Riprova.",
                ttl_ms=9000,
            )
            self._cleanup_overlay_request(request_id)

    def _select_area_rect(self) -> tuple[int, int, int, int] | None:
        selector = tk.Toplevel(self.root)
        selector.overrideredirect(True)
        selector.attributes("-topmost", True)
        try:
            selector.attributes("-alpha", 0.18)
        except Exception:
            pass
        width = selector.winfo_screenwidth()
        height = selector.winfo_screenheight()
        selector.geometry(f"{width}x{height}+0+0")
        selector.configure(bg="black")

        canvas = tk.Canvas(selector, bg="black", highlightthickness=0, cursor="crosshair")
        canvas.pack(fill=tk.BOTH, expand=True)

        state: dict[str, Any] = {"start": None, "rect_id": None, "bbox": None}

        def on_press(event: tk.Event[Any]) -> None:
            state["start"] = (event.x, event.y)
            if state["rect_id"] is not None:
                canvas.delete(state["rect_id"])
            state["rect_id"] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#60a5fa", width=2)

        def on_drag(event: tk.Event[Any]) -> None:
            start = state.get("start")
            rect_id = state.get("rect_id")
            if start is None or rect_id is None:
                return
            canvas.coords(rect_id, start[0], start[1], event.x, event.y)

        def on_release(event: tk.Event[Any]) -> None:
            start = state.get("start")
            if start is None:
                selector.destroy()
                return
            x1, y1 = start
            x2, y2 = event.x, event.y
            left, right = sorted((int(x1), int(x2)))
            top, bottom = sorted((int(y1), int(y2)))
            if (right - left) >= 8 and (bottom - top) >= 8:
                state["bbox"] = (left, top, right, bottom)
            selector.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        selector.bind("<Escape>", lambda _e: selector.destroy())
        selector.focus_force()
        selector.grab_set()
        self.root.wait_window(selector)
        return state.get("bbox")

    def _capture_area_screenshot_path(self, log_errors: bool = True) -> str | None:
        system_name = platform.system().lower()
        if system_name == "darwin":
            tmp = tempfile.NamedTemporaryFile(prefix="gemini-shot-", suffix=".png", delete=False)
            path = tmp.name
            tmp.close()
            try:
                Path(path).unlink(missing_ok=True)
                result = subprocess.run(
                    ["screencapture", "-i", "-x", path],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                stderr = (result.stderr or "").strip()
                if result.returncode != 0:
                    lowered = stderr.lower()
                    if log_errors:
                        if "cancel" in lowered:
                            self._append_log("System", "Screenshot canceled")
                        elif "not authorized" in lowered or "permission" in lowered:
                            self._append_log(
                                "Error",
                                "Screenshot blocked: abilita Screen Recording per BluetoothGeminiChat in macOS Settings.",
                            )
                        else:
                            detail = stderr if stderr else f"exit code {result.returncode}"
                            self._append_log("Error", f"Screenshot failed: {detail}")
                    Path(path).unlink(missing_ok=True)
                    return None
                if not Path(path).exists() or Path(path).stat().st_size <= 0:
                    if log_errors:
                        self._append_log("Error", "Screenshot non disponibile: nessun file creato.")
                    Path(path).unlink(missing_ok=True)
                    return None
                return path
            except Exception as exc:
                if log_errors:
                    self._append_log("Error", f"Screenshot failed: {exc}")
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
                return None

        if ImageGrab is None:
            if log_errors:
                self._append_log("Error", "Area screenshot requires Pillow ImageGrab")
            return None

        try:
            full = ImageGrab.grab(all_screens=True)  # type: ignore[union-attr]
            bbox = self._select_area_rect()
            if bbox is None:
                if log_errors:
                    self._append_log("System", "Screenshot canceled")
                return None
            cropped = full.crop(bbox)
            fd, path = tempfile.mkstemp(prefix="gemini-shot-", suffix=".png")
            os.close(fd)
            cropped.save(path, format="PNG")
            return path
        except Exception as exc:
            if log_errors:
                self._append_log("Error", f"Screenshot failed: {exc}")
            return None

    def _append_log(self, role: str, text: str) -> None:
        clean = str(text).strip()
        if not clean:
            return

        role_key = role.strip().lower()
        if role_key == "you":
            role_tag, msg_tag = "role_you", "msg_you"
        elif role_key == "gemini":
            role_tag, msg_tag = "role_gemini", "msg_gemini"
        elif role_key == "thought":
            role_tag, msg_tag = "role_thought", "msg_thought"
        elif role_key.startswith("phone"):
            role_tag, msg_tag = "role_phone", "msg_phone"
        elif role_key in {"error", "phone error"}:
            role_tag, msg_tag = "role_error", "msg_error"
        else:
            role_tag, msg_tag = "role_system", "msg_system"

        self.chat_log.configure(state='normal')
        
        if role_key == "thought":
            is_streaming = clean.endswith("▌")
            word_count = len(clean.split())
            btn_text = f"[-] Thinking... ({word_count} w)" if is_streaming else f"[+] Mostra Ragionamento ({word_count} parole)"
            
            tag_name = f"thought_block_{self._md_link_seq}"
            btn_tag = f"thought_btn_{self._md_link_seq}"
            self._md_link_seq += 1
            
            self.chat_log.insert(tk.END, f"{btn_text}\n", (role_tag, btn_tag))
            
            # Config block element initially hidden if not streaming
            self.chat_log._textbox.tag_configure(tag_name, elide=not is_streaming)
            self.chat_log.insert(tk.END, f"{clean}\n\n", (msg_tag, tag_name))
            
            # Click bound to button
            def toggle_thought(e, t=tag_name):
                # get direct tag conf from actual text widget
                state = self.chat_log._textbox.tag_cget(t, "elide")
                new_state = False if str(state) == "1" else True
                self.chat_log._textbox.tag_configure(t, elide=new_state)

            self.chat_log._textbox.tag_bind(btn_tag, "<Button-1>", toggle_thought)
            self.chat_log._textbox.tag_bind(btn_tag, "<Enter>", lambda _e: self.chat_log.configure(cursor="hand2"))
            self.chat_log._textbox.tag_bind(btn_tag, "<Leave>", lambda _e: self.chat_log.configure(cursor="xterm"))
        else:
            self.chat_log.insert(tk.END, f"{role}\n", role_tag)
            if role_key in {"gemini", "assistant"}:
                self._insert_markdown_message(clean, msg_tag)
                self.chat_log.insert(tk.END, "\n", msg_tag)
            else:
                self.chat_log.insert(tk.END, f"{clean}\n\n", msg_tag)

        self.chat_log.see(tk.END)
        self.chat_log.configure(state='disabled')

    def _insert_markdown_message(self, text: str, base_tag: str) -> None:
        inline_pattern = re.compile(
            r"(\[([^\]]+)\]\((https?://[^)\s]+)\)|\*\*([^*]+)\*\*|`([^`]+)`)"
        )
        lines = text.splitlines()
        in_code = False
        for raw_line in lines:
            line = raw_line.rstrip("\n")
            stripped = line.strip()

            if stripped.startswith("```"):
                in_code = not in_code
                continue

            if in_code:
                self.chat_log.insert(tk.END, f"{line}\n", (base_tag, "md_code_block"))
                continue

            if stripped.startswith("# "):
                self.chat_log.insert(tk.END, stripped[2:] + "\n", (base_tag, "md_h1"))
                continue
            if stripped.startswith("## "):
                self.chat_log.insert(tk.END, stripped[3:] + "\n", (base_tag, "md_h2"))
                continue
            if stripped.startswith("### "):
                self.chat_log.insert(tk.END, stripped[4:] + "\n", (base_tag, "md_h3"))
                continue

            if stripped.startswith("- ") or stripped.startswith("* "):
                line = "• " + stripped[2:]
            elif re.match(r"^\d+\.\s+", stripped):
                line = stripped
            elif stripped.startswith("> "):
                line = "▎" + stripped[2:]

            cursor = 0
            for match in inline_pattern.finditer(line):
                start, end = match.span()
                if start > cursor:
                    self.chat_log.insert(tk.END, line[cursor:start], base_tag)

                link_label = match.group(2)
                link_url = match.group(3)
                bold_text = match.group(4)
                code_text = match.group(5)

                if link_label and link_url:
                    self._insert_link(link_label, link_url, base_tag)
                elif bold_text:
                    self.chat_log.insert(tk.END, bold_text, (base_tag, "md_bold"))
                elif code_text:
                    self.chat_log.insert(tk.END, code_text, (base_tag, "md_inline_code"))

                cursor = end

            if cursor < len(line):
                self.chat_log.insert(tk.END, line[cursor:], base_tag)
            self.chat_log.insert(tk.END, "\n", base_tag)

        self.chat_log.insert(tk.END, "\n", base_tag)

    def _insert_link(self, label: str, url: str, base_tag: str) -> None:
        tag_name = f"md_link_{self._md_link_seq}"
        self._md_link_seq += 1
        self._md_link_urls[tag_name] = url
        self.chat_log.insert(tk.END, label, (base_tag, "md_link", tag_name))
        self.chat_log._textbox.tag_bind(tag_name, "<Button-1>", lambda _e, t=tag_name: self._open_md_link(t))
        self.chat_log._textbox.tag_bind(tag_name, "<Enter>", lambda _e: self.chat_log.configure(cursor="hand2"))
        self.chat_log._textbox.tag_bind(tag_name, "<Leave>", lambda _e: self.chat_log.configure(cursor="xterm"))

    def _open_md_link(self, tag_name: str) -> None:
        url = self._md_link_urls.get(tag_name)
        if not url:
            return
        try:
            webbrowser.open(url, new=2)
        except Exception:
            self._append_log("System", f"Open link manually: {url}")

    def _clear_chat_widget(self) -> None:
        self.chat_log.configure(state='normal')
        self.chat_log.delete("1.0", tk.END)
        self.chat_log.configure(state='disabled')

    def _render_active_chat(self) -> None:
        self._clear_chat_widget()
        messages = self.sessions_store.get_messages(self.active_session_id)
        for msg in messages:
            role = msg.get("role", "")
            text = msg.get("text", "")
            if role == "user":
                self._append_log("You", text)
            elif role == "assistant":
                self._append_log("Gemini", text)
            elif role == "thought":
                self._append_log("Thought", text)
            elif role == "phone":
                self._append_log("Phone", text)
            elif role == "error":
                self._append_log("Error", text)
            else:
                self._append_log("System", text)

        preview = self._streaming_preview_by_session.get(self.active_session_id, "").strip()
        if preview:
            self._append_log("Gemini", f"{preview}\n▌")
        thought_preview = self._streaming_thought_by_session.get(self.active_session_id, "").strip()
        if thought_preview and self.show_thoughts_var.get():
            self._append_log("Thought", f"{thought_preview}\n▌")
        self._refresh_stop_button()

    def on_scan(self) -> None:
        self.devices_list.delete(0, tk.END)
        self.devices = []
        self.client.scan_devices()

    def on_connect(self) -> None:
        selected = self.devices_list.curselection()
        if not selected:
            if self._last_connected_address:
                self._append_log("System", f"Connecting to last known device: {self._last_connected_address}")
                self.client.connect(self._last_connected_address)
                return
            self._append_log("System", "Select a device first")
            return

        idx = selected[0]
        device = self.devices[idx]
        self.client.connect(device["address"])

    def on_disconnect(self) -> None:
        self.client.disconnect()

    def _ensure_active_session(self, source: str = "action") -> str:
        current_id = str(getattr(self, "active_session_id", "") or "").strip()
        sessions = self.sessions_store.list_sessions()
        known_ids = {str(s.get("id", "")) for s in sessions}
        if current_id and current_id in known_ids:
            return current_id

        if sessions:
            recovered_id = str(sessions[0]["id"])
            self.active_session_id = recovered_id
            self.sessions_store.set_active_session(recovered_id)
            self._refresh_sessions_list(recovered_id)
            self._render_active_chat()
            self._refresh_memory_label()
            self._append_log("System", f"Recovered active chat for {source}")
            return recovered_id

        session_id = self.sessions_store.create_session("Nuova chat")
        self.active_session_id = session_id
        self._refresh_sessions_list(session_id)
        self._render_active_chat()
        self._refresh_memory_label()
        self._append_log("System", f"Auto-created chat for {source}")
        return session_id

    def on_new_chat(self) -> None:
        session_id = self.sessions_store.create_session("Nuova chat")
        self.active_session_id = session_id
        self._refresh_sessions_list(session_id)
        self._render_active_chat()
        self._refresh_memory_label()
        self._append_log("System", "New chat created")

    def on_rename_chat(self) -> None:
        sessions = self.sessions_store.list_sessions()
        current = next((s for s in sessions if s["id"] == self.active_session_id), None)
        default_title = str(current["title"]) if current else "Nuova chat"
        title = simpledialog.askstring("Rename chat", "Nuovo titolo:", initialvalue=default_title, parent=self.root)
        if title is None:
            return
        self.sessions_store.rename_session(self.active_session_id, title)
        self._refresh_sessions_list(self.active_session_id)

    def on_delete_chat(self) -> None:
        from tkinter import messagebox
        if not messagebox.askyesno("Delete Chat", "Are you sure you want to delete this conversation?", parent=self.root):
            return
            
        removed = self.sessions_store.delete_session(self.active_session_id)
        if not removed:
            return
        self.active_session_id = self.sessions_store.active_session_id
        self._refresh_sessions_list(self.active_session_id)
        self._render_active_chat()
        self._refresh_memory_label()
        self._append_log("System", "Chat deleted")

    def _refresh_sessions_list(self, selected_session_id: str | None = None) -> None:
        sessions = self.sessions_store.list_sessions()
        self.chats_list.delete(0, tk.END)
        self._session_ids_in_view = []
        selected_idx: int | None = None
        target = selected_session_id or self.active_session_id
        
        try:
            query = self.search_var.get().lower().strip()
        except AttributeError:
            query = ""
            
        for idx, session in enumerate(sessions):
            title = str(session["title"])
            count = int(session["messageCount"])
            
            if query and query not in title.lower():
                continue
                
            self.chats_list.insert(tk.END, f"{title} ({count})")
            self._session_ids_in_view.append(session["id"])
            if session["id"] == target:
                selected_idx = len(self._session_ids_in_view) - 1
                
        if selected_idx is not None:
            self.chats_list.selection_clear(0, tk.END)
            self.chats_list.selection_set(selected_idx)

    def _on_session_selected(self, _: tk.Event[Any]) -> None:
        selected = self.chats_list.curselection()
        if not selected:
            return
        idx = selected[0]
        if idx < 0 or idx >= len(self._session_ids_in_view):
            return
        session_id = self._session_ids_in_view[idx]
        if session_id == self.active_session_id:
            return
        self.active_session_id = session_id
        self.sessions_store.set_active_session(session_id)
        self._render_active_chat()
        self._refresh_memory_label()

    def on_attach_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Select an image",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self._set_selected_image(path)

    def _set_selected_image(self, path: str) -> None:
        self.selected_image_path = path
        try:
            size_kb = os.path.getsize(path) / 1024.0
            self.image_var.set(f"Image: {os.path.basename(path)} ({size_kb:.1f} KB)")
        except OSError:
            self.image_var.set(f"Image: {os.path.basename(path)}")
        self._refresh_context_preview()
        self._append_log("System", f"Selected image: {os.path.basename(path)}")

    def on_quick_screenshot(self) -> None:
        path = self._capture_area_screenshot_path(log_errors=True)
        if path is None:
            return
        self._set_selected_image(path)

    def _send_overlay_request(
        self,
        prompt: str,
        source: str,
        status_text: str,
        image_path: str | None = None,
        fail_text: str = "Invio richiesta fallito",
    ) -> bool:
        if not self.connected:
            self._show_overlay_message("Bridge non connesso", ttl_ms=3500)
            if image_path:
                try:
                    Path(image_path).unlink(missing_ok=True)
                except OSError:
                    pass
            return False

        session_id = self._ensure_active_session(source)
        selected_model = self.model_var.get().strip() if hasattr(self, "model_var") else MODEL_PRESETS[0]
        model_override = selected_model if selected_model and selected_model != MODEL_PRESETS[0] else None
        thinking_budget = self._get_thinking_budget()
        thinking_enabled = self.thinking_enabled.get()
        include_thoughts = thinking_enabled and self.show_thoughts_var.get()

        try:
            request_id = self.client.send_prompt(
                prompt,
                model=model_override,
                image_path=image_path,
                image_target_bytes=38 * 1024,
                image_max_dimension=640,
                enable_web_search=self.web_search_enabled.get(),
                thinking_enabled=thinking_enabled,
                thinking_budget=thinking_budget,
                include_thoughts=include_thoughts,
            )
        except Exception as exc:
            self._show_overlay_message(f"{fail_text}: {exc}", ttl_ms=5000)
            if image_path:
                try:
                    Path(image_path).unlink(missing_ok=True)
                except OSError:
                    pass
            return False

        self._overlay_request_ids.add(request_id)
        now = time.monotonic()
        self._overlay_started_at[request_id] = now
        self._overlay_last_update_at[request_id] = now
        if image_path:
            self._overlay_image_paths_by_request[request_id] = image_path
        self._track_pending_request(request_id, session_id)
        self._show_overlay_message(status_text, ttl_ms=0)
        return True

    def _capture_clipboard_image_path(self, log_errors: bool = True) -> str | None:
        if ImageGrab is None:
            if log_errors:
                self._append_log("System", "Clipboard image unavailable: install Pillow")
            return None

        try:
            image = ImageGrab.grabclipboard()  # type: ignore[union-attr]
            if image is None or not hasattr(image, "save"):
                return None
            fd, path = tempfile.mkstemp(prefix="gemini-clip-", suffix=".png")
            os.close(fd)
            image.save(path, format="PNG")
            return path
        except Exception as exc:
            if log_errors:
                self._append_log("Error", f"Clipboard import failed: {exc}")
            return None

    def on_hotkey_overlay_triggered(self, prompt_override: str | None = None) -> None:
        if not self.connected:
            self._show_overlay_message("Bridge non connesso", ttl_ms=3500)
            return

        path = self._capture_area_screenshot_path(log_errors=False)
        if path is None:
            self._show_overlay_message("Screenshot annullato", ttl_ms=2500)
            return

        prompt = (
            prompt_override.strip()
            if prompt_override and prompt_override.strip()
            else "Analizza rapidamente questo screenshot. Rispondi in italiano con massimo 5 righe."
        )
        self._send_overlay_request(
            prompt=prompt,
            source="Shot+Ask",
            status_text="Analisi screenshot in corso...",
            image_path=path,
            fail_text="Invio screenshot fallito",
        )

    def on_hotkey_clipboard_triggered(self, prompt_override: str | None = None) -> None:
        if not self.connected:
            self._show_overlay_message("Bridge non connesso", ttl_ms=3500)
            return

        override = prompt_override.strip() if prompt_override else ""
        clip_text = ""
        try:
            clip_text = self.root.clipboard_get().strip()
        except tk.TclError:
            clip_text = ""

        if clip_text:
            if override:
                prompt = f"{override}\n\nClipboard:\n{clip_text}"
            else:
                prompt = (
                    "Analizza rapidamente questo testo copiato negli appunti. "
                    "Rispondi in italiano con massimo 5 righe.\n\n"
                    f"{clip_text}"
                )
            self._send_overlay_request(
                prompt=prompt,
                source="Clip+Ask",
                status_text="Analisi clipboard in corso...",
                image_path=None,
                fail_text="Invio clipboard fallito",
            )
            return

        path = self._capture_clipboard_image_path(log_errors=False)
        if path is None:
            self._show_overlay_message("Clipboard vuota o formato non supportato", ttl_ms=3200)
            return

        prompt = (
            override
            if override
            else "Analizza rapidamente questa immagine dagli appunti. Rispondi in italiano con massimo 5 righe."
        )
        self._send_overlay_request(
            prompt=prompt,
            source="Clip+Ask",
            status_text="Analisi clipboard in corso...",
            image_path=path,
            fail_text="Invio clipboard fallito",
        )

    def on_clipboard_send(self) -> None:
        text = ""
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            text = ""

        if text:
            self.prompt_entry.delete("1.0", tk.END)
            self.prompt_entry.insert("1.0", text)
            self.on_send()
            return

        path = self._capture_clipboard_image_path(log_errors=True)
        if path is None:
            self._append_log("System", "Clipboard empty or unsupported format")
            return
        self._set_selected_image(path)
        self.prompt_entry.delete("1.0", tk.END)
        self.prompt_entry.insert("1.0", "Descrivi questo screenshot.")
        self.on_send()

    def _auto_install_quick_action(self) -> None:
        if platform.system().lower() != "darwin":
            return
        self.on_install_quick_action(silent=True)

    def on_install_quick_action(self, silent: bool = False) -> None:
        if platform.system().lower() != "darwin":
            if not silent:
                self._append_log("System", "Quick Action auto-install is only available on macOS")
            return

        installer = Path(__file__).with_name("install_macos_quick_action.py")
        if not installer.exists():
            if not silent:
                self._append_log("Error", "Quick Action installer script not found")
            return

        cmd = ["python3", str(installer), "--quiet"]
        try:
            result = subprocess.run(cmd, check=False, capture_output=True, text=True)
            if result.returncode == 0:
                if not silent:
                    self._append_log("System", "Right-click Quick Action installed/updated")
                return

            stderr = (result.stderr or "").strip()
            if not silent:
                if stderr:
                    self._append_log("Error", f"Quick Action install failed: {stderr}")
                else:
                    self._append_log("Error", "Quick Action install failed")
        except Exception as exc:
            if not silent:
                self._append_log("Error", f"Quick Action install failed: {exc}")

    def _consume_quick_inbox(self) -> None:
        path = self._quick_inbox_path
        if not path.exists():
            self._quick_inbox_offset = 0
            return

        try:
            size = path.stat().st_size
            if size < self._quick_inbox_offset:
                self._quick_inbox_offset = 0
        except OSError:
            return

        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(self._quick_inbox_offset)
                lines = handle.readlines()
                self._quick_inbox_offset = handle.tell()
        except OSError:
            return

        for line in lines:
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            payload_type = str(payload.get("type", "quick_send")).strip().lower()
            if payload_type == "quick_send":
                text = str(payload.get("text", "")).strip()
                if not text:
                    continue
                self.events.put({"type": "quick_send", "text": text})
                continue
            if payload_type in {"quick_overlay", "quick_shot_ask", "hotkey_overlay"}:
                prompt = str(payload.get("prompt", "")).strip()
                self.events.put({"type": "quick_overlay", "prompt": prompt})
                continue
            if payload_type in {"quick_clipboard_overlay", "quick_clipboard", "quick_clip_ask"}:
                prompt = str(payload.get("prompt", "")).strip()
                self.events.put({"type": "quick_clipboard_overlay", "prompt": prompt})
                continue
            if payload_type == "toggle_visibility":
                self.events.put({"type": "toggle_visibility"})

    def on_clear_image(self) -> None:
        self.selected_image_path = None
        self.image_var.set("Image: none")
        self._refresh_context_preview()

    def on_add_pdf(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select PDF documents",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not paths:
            return

        added = 0
        for path in paths:
            if path not in self.selected_pdf_paths:
                self.selected_pdf_paths.append(path)
                added += 1

        if added:
            self._append_log("System", f"Added {added} PDF(s) to context")
        self._refresh_pdf_label()
        self._refresh_context_preview()

    def on_clear_pdfs(self) -> None:
        self.selected_pdf_paths = []
        self._refresh_pdf_label()
        self._refresh_context_preview()
        self._append_log("System", "PDF context cleared")

    def _on_file_drop(self, event) -> None:
        data = getattr(event, 'data', "")
        if not data:
            return
        
        paths = []
        if "{" in data:
            matches = re.findall(r'\{([^}]+)\}', data)
            if matches:
                 paths = matches
            else:
                 paths = data.split()
        else:
            import shlex
            try:
                paths = shlex.split(data)
            except ValueError:
                paths = [data]
                
        added_pdfs = 0
        for path in paths:
            path = path.strip()
            if not os.path.isfile(path):
                continue
            
            ext = path.lower().split('.')[-1]
            if ext == "pdf":
                if path not in self.selected_pdf_paths:
                    self.selected_pdf_paths.append(path)
                    added_pdfs += 1
            elif ext in ["png", "jpg", "jpeg", "webp", "gif", "bmp"]:
                self._set_selected_image(path)
                
        if added_pdfs > 0:
            self._append_log("System", f"Added {added_pdfs} PDF(s) to context via drag & drop")
            self._refresh_pdf_label()
            self._refresh_context_preview()

    def _refresh_pdf_label(self) -> None:
        if not self.selected_pdf_paths:
            self.pdf_var.set("PDF: none")
            return

        names = [os.path.basename(path) for path in self.selected_pdf_paths]
        if len(names) == 1:
            self.pdf_var.set(f"PDF: {names[0]}")
        else:
            self.pdf_var.set(f"PDF: {len(names)} selected ({names[0]} + others)")

    def _refresh_context_preview(self) -> None:
        parts: list[str] = []
        selected_model = self.model_var.get().strip() if hasattr(self, "model_var") else MODEL_PRESETS[0]
        if selected_model and selected_model != MODEL_PRESETS[0]:
            parts.append(f"model: {selected_model}")
        if self.selected_image_path:
            parts.append(f"image: {os.path.basename(self.selected_image_path)}")
        if self.selected_pdf_paths:
            parts.append(f"pdfs: {len(self.selected_pdf_paths)}")
        active_container_name = self._active_container_name()
        if self._active_container_id and active_container_name:
            parts.append(f"container: {active_container_name}")
        elif self._active_container_id:
            parts.append("container: remote")
        if self.web_search_enabled.get():
            parts.append("web search: on")
        if self.thinking_enabled.get():
            budget = self._get_thinking_budget()
            if budget is None:
                parts.append("thinking: auto")
            else:
                parts.append(f"thinking: {budget}")
            if self.show_thoughts_var.get():
                parts.append("thought trace: on")

        if parts:
            self.context_preview_var.set("Active context -> " + ", ".join(parts))
        else:
            self.context_preview_var.set("No active attachments")

    def _get_thinking_budget(self) -> int | None:
        if not self.thinking_enabled.get():
            return None
        if self.thinking_auto_var.get():
            return -1
        try:
            value = int(self.thinking_budget_var.get().strip())
        except Exception:
            value = 1024
        value = max(0, min(24576, value))
        self.thinking_budget_var.set(str(value))
        return value

    def _refresh_memory_label(self) -> None:
        count = len(self.sessions_store.recent_turns(self.active_session_id, max_items=10000, max_chars=10_000_000))
        if count == 0:
            self.memory_var.set("Memory: empty")
            return
        self.memory_var.set(f"Memory: {count} turn(s)")

    def _estimate_input_tokens(
        self,
        prompt: str,
        memory_turns: list[dict[str, str]],
        context_blocks: list[dict[str, Any]],
    ) -> tuple[int, int, int]:
        text_chars = len(prompt)
        for turn in memory_turns:
            text_chars += len(str(turn.get("text", "")))
        for block in context_blocks:
            text_chars += len(str(block.get("text", "")))

        text_tokens = max(1, int(round(text_chars / 4.0)))

        image_tokens = 0
        if self.selected_image_path:
            try:
                size_bytes = Path(self.selected_image_path).stat().st_size
                image_tokens = max(256, int(size_bytes / 1200))
            except OSError:
                image_tokens = 512

        total = text_tokens + image_tokens
        return total, text_tokens, image_tokens

    def on_clear_memory(self) -> None:
        self.sessions_store.clear_messages(self.active_session_id)
        self._render_active_chat()
        self._refresh_memory_label()
        self._refresh_sessions_list(self.active_session_id)
        self._append_log("System", "Current chat memory cleared")

    def on_send(self) -> None:
        prompt = self.prompt_entry.get("1.0", tk.END).strip()
        if not prompt and self.selected_image_path is not None:
            prompt = "Describe this image."

        if not prompt:
            return
        if not self.connected:
            self._append_log("System", "Not connected")
            return

        session_id = self._ensure_active_session("send")
        memory_turns = self.sessions_store.recent_turns(session_id, max_items=10, max_chars=2600)
        context_blocks = []

        # --- Active container takes priority over per-session PDFs ---
        use_container = self._active_container_id is not None

        if not use_container:
            # Legacy: system instructions + pinned PDFs + session PDFs
            sys_instr = self.system_instructions_var.get().strip()
            if sys_instr:
                context_blocks.append({"type": "text", "text": f"System Instructions:\n{sys_instr}"})

            all_pdf_paths = list(self.pinned_pdf_paths)
            for p in self.selected_pdf_paths:
                if p not in all_pdf_paths:
                    all_pdf_paths.append(p)

            if all_pdf_paths:
                try:
                    blocks = self.pdf_context_engine.build_context(prompt, all_pdf_paths)
                    context_blocks.extend(blocks)
                except ValueError as exc:
                    self._append_log("Error", str(exc))
                    return
        else:
            # Container mode: inject system instructions only; Android does retrieval
            sys_instr = self.system_instructions_var.get().strip()
            if sys_instr:
                context_blocks.append({"type": "text", "text": f"System Instructions:\n{sys_instr}"})

        est_total, est_text, est_image = self._estimate_input_tokens(prompt, memory_turns, context_blocks)
        if est_image > 0:
            self._append_log(
                "System",
                f"Estimated input tokens: ~{est_total} (text ~{est_text}, image ~{est_image})",
            )
        else:
            self._append_log("System", f"Estimated input tokens: ~{est_total}")

        if est_total > 14_000:
            self._append_log(
                "System",
                "Large request detected: higher timeout risk. Consider shorter prompt or fewer PDF blocks.",
            )

        thinking_budget = self._get_thinking_budget()
        thinking_enabled = self.thinking_enabled.get()
        include_thoughts = thinking_enabled and self.show_thoughts_var.get()
        selected_model = self.model_var.get().strip()
        model_override = selected_model if selected_model and selected_model != 'phone-default' else None

        try:
            request_id = self.client.send_prompt(
                prompt,
                model=model_override,
                image_path=self.selected_image_path,
                context_blocks=context_blocks or None,
                memory_turns=memory_turns or None,
                enable_web_search=self.web_search_enabled.get(),
                thinking_enabled=thinking_enabled,
                thinking_budget=thinking_budget,
                include_thoughts=include_thoughts,
                active_container_id=self._active_container_id,
                active_container_name=self._active_container_name(),
            )
        except ValueError as exc:
            self._append_log("Error", str(exc))
            from tkinter import messagebox
            messagebox.showerror("Errore di Invio", str(exc), parent=self.root)
            return

        self._streaming_preview_by_session.pop(session_id, None)
        self._streaming_thought_by_session.pop(session_id, None)
        self.sessions_store.add_message(session_id, "user", prompt)
        self._refresh_sessions_list(session_id)
        if session_id == self.active_session_id:
            self._render_active_chat()
            if self.selected_image_path is not None:
                self._append_log("System", f"Image attached: {os.path.basename(self.selected_image_path)}")
            if context_blocks:
                self._append_log("System", f"PDF context blocks sent: {len(context_blocks)}")
            if memory_turns:
                self._append_log("System", f"Memory turns sent: {len(memory_turns)}")
            if self.web_search_enabled.get():
                self._append_log("System", "Web search tool enabled")
            if model_override is not None:
                self._append_log("System", f"Model override: {model_override}")
            if thinking_enabled:
                if thinking_budget is None or thinking_budget < 0:
                    self._append_log("System", "Thinking mode enabled (auto budget)")
                else:
                    self._append_log("System", f"Thinking mode enabled (budget {thinking_budget})")
                if include_thoughts:
                    self._append_log("System", "Thought trace enabled")
            self._append_log("System", f"Request queued ({request_id})")

        self._track_pending_request(request_id, session_id)
        self._refresh_memory_label()

        self.prompt_entry.delete("1.0", tk.END)
        self.on_clear_image()

    def on_stop_active_request(self) -> None:
        request_id = self._latest_pending_request_for_session(self.active_session_id)
        if request_id is None:
            self._append_log("System", "Nessuna richiesta attiva da fermare")
            self._refresh_stop_button()
            return
        if not self.connected:
            self._append_log("System", "Bridge non connesso")
            return
        try:
            self.client.cancel_request(request_id)
            self._append_log("System", f"Stop requested ({request_id})")
        except Exception as exc:
            self._append_log("Error", f"Stop request failed: {exc}")

    def _consume_toggle_flag(self) -> None:
        if not self._toggle_flag_path.exists():
            return
        try:
            mtime = self._toggle_flag_path.stat().st_mtime
            if self._toggle_flag_mtime == 0.0:
                self._toggle_flag_mtime = mtime
                return
            if mtime > self._toggle_flag_mtime:
                self._toggle_flag_mtime = mtime
                self.events.put({"type": "toggle_visibility"})
        except OSError:
            pass
    def _consume_clipboard_flag(self) -> None:
        if not self._clipboard_flag_path.exists():
            return
        try:
            mtime = self._clipboard_flag_path.stat().st_mtime
            if self._clipboard_flag_mtime == 0.0:
                self._clipboard_flag_mtime = mtime
                return
            if mtime > self._clipboard_flag_mtime:
                self._clipboard_flag_mtime = mtime
                import pyperclip
                text = pyperclip.paste().strip()
                if text:
                    self.events.put({"type": "force_visibility"})
                    self.events.put({"type": "quick_send", "text": f"Analizza e rispondi a questo testo copiato negli appunti:\n\n{text}"})
        except OSError:
            pass

    def _poll_events(self) -> None:
        self._consume_quick_inbox()
        self._consume_toggle_flag()
        self._consume_clipboard_flag()
        self._check_overlay_request_timeouts()
        self._present_overlay_window()

        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)

        self.root.after(100, self._poll_events)

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")

        if event_type == "force_visibility":
            self._force_app_visibility()
            return

        if event_type == "toggle_visibility":
            self._toggle_app_visibility()
            return

        if event_type == "hotkey_overlay":
            self.on_hotkey_overlay_triggered()
            return

        if event_type == "quick_overlay":
            self.on_hotkey_overlay_triggered(str(event.get("prompt", "")))
            return

        if event_type == "quick_clipboard_overlay":
            self.on_hotkey_clipboard_triggered(str(event.get("prompt", "")))
            return

        if event_type == "status":
            status = event.get("text", "")
            self.status_var.set(status)
            self._append_log("System", status)
            return

        if event_type == "error":
            message = event.get("text", "Unknown error")
            if self._overlay_request_ids and isinstance(message, str) and message:
                self._show_overlay_message(f"Errore bridge: {message}", ttl_ms=8000)
                self._cleanup_all_overlay_requests()
            self._clear_all_pending_requests()
            self._append_log("Error", message)
            return

        if event_type == "scan_result":
            self.devices = event.get("devices", [])
            self.devices_list.delete(0, tk.END)
            selected_idx: int | None = None
            for idx, device in enumerate(self.devices):
                label = f"{device['name']} ({device['address']})"
                self.devices_list.insert(tk.END, label)
                if self._last_connected_address and device.get("address") == self._last_connected_address:
                    selected_idx = idx
            if selected_idx is not None:
                self.devices_list.selection_clear(0, tk.END)
                self.devices_list.selection_set(selected_idx)
            if self.devices:
                self._append_log("System", f"Found {len(self.devices)} Gemini bridge device(s)")
            else:
                self._append_log(
                    "System",
                    "No Gemini bridge found. Keep Android bridge service active and retry Scan.",
                )
            return

        if event_type == "connected":
            self.connected = True
            address = str(event.get("address", "")).strip()
            if address:
                self._last_connected_address = address
                self._update_settings({"last_connected_address": address})
            device = event.get("device", "device")
            packet = event.get("max_packet_size", "?")
            self.status_var.set(f"Connected: {device}")
            self.link_var.set("Link: probing...")
            self._last_link_state = "healthy"
            self._append_log("System", f"Connected ({device}), packet size: {packet}")
            return

        if event_type == "disconnected":
            self.connected = False
            self.status_var.set("Disconnected")
            self.link_var.set("Link: offline")
            if self._overlay_request_ids:
                self._show_overlay_message("Bridge disconnesso durante Shot+Ask", ttl_ms=8000)
                self._cleanup_all_overlay_requests()
            self._clear_all_pending_requests()
            self._append_log("System", "Disconnected")
            return

        if event_type == "link_quality":
            rtt = event.get("rtt_ms")
            if isinstance(rtt, int):
                self.link_var.set(f"Link RTT: {rtt} ms")
            return

        if event_type == "link_status":
            state = str(event.get("state", "unknown"))
            if state != self._last_link_state:
                self._last_link_state = state
                text = event.get("text")
                if isinstance(text, str) and text:
                    self._append_log("System", text)
            return

        if event_type == "transfer_progress":
            percent = event.get("percent")
            current = event.get("current_packets")
            total = event.get("total_packets")
            request_id = event.get("request_id", "")
            if isinstance(percent, int) and isinstance(current, int) and isinstance(total, int):
                # Update status bar always
                self.status_var.set(f"📡 Invio... {percent}% ({current}/{total} pacchetti)")
                if request_id and request_id in self._overlay_request_ids:
                    self._overlay_last_update_at[request_id] = time.monotonic()
                    self._show_overlay_message(
                        f"Invio screenshot... {percent}% ({current}/{total})",
                        ttl_ms=0,
                    )
                # Update dedicated progress dialog if this is a container transfer
                if request_id == self._container_transfer_request_id:
                    self._update_transfer_dialog(percent, current, total)
            return

        if event_type == "sent":
            if self.connected:
                self.status_var.set("Connected")
            return

        if event_type == "incoming":
            message = event.get("message", {})
            message_type = message.get("type")
            message_id = str(message.get("messageId", "")).strip()
            if not message_id and message_type in {"status", "partial", "result", "error"} and len(self._overlay_request_ids) == 1:
                # Fallback for malformed replies missing messageId.
                message_id = next(iter(self._overlay_request_ids))
            if message_id and message_id in self._overlay_request_ids:
                self._overlay_last_update_at[message_id] = time.monotonic()
                if message_type == "partial":
                    channel = str(message.get("channel", "answer")).strip().lower()
                    if channel != "thought":
                        partial_text = str(message.get("text", "")).strip()
                        if partial_text:
                            self._show_overlay_message(partial_text + "\n▌", ttl_ms=0)
                    return
                if message_type == "result":
                    response_text = str(message.get("text", "")).strip()
                    if response_text:
                        self._show_overlay_message(response_text, ttl_ms=20000)
                    self._cleanup_overlay_request(message_id)
                    return
                if message_type == "error":
                    error_text = str(message.get("error", "Unknown error")).strip()
                    if error_text:
                        self._show_overlay_message(f"Errore: {error_text}", ttl_ms=8000)
                    self._cleanup_overlay_request(message_id)
                    return
                if message_type == "status":
                    state = str(message.get("state", "processing")).strip()
                    if state:
                        self._show_overlay_message(state, ttl_ms=0)
                    if state.lower().startswith("canceled"):
                        self._cleanup_overlay_request(message_id)
                    return

            target_session = self._pending_request_session.get(message_id, self.active_session_id)

            if message_type == "container_ack":
                chunk_count = message.get("chunkCount", "?")
                container_id = str(message.get("containerId", "")).strip()
                self._close_transfer_dialog(success=True)
                if container_id:
                    self._active_container_id = container_id
                    self._refresh_container_list()
                    self._refresh_context_preview()
                self._append_log("System", f"📱 Container confermato dal telefono ({chunk_count} chunk salvati).")
                return

            if message_type == "container_list":
                raw_containers = message.get("containers", [])
                parsed: list[dict[str, Any]] = []
                if isinstance(raw_containers, list):
                    for item in raw_containers:
                        if not isinstance(item, dict):
                            continue
                        cid = str(item.get("id", "")).strip()
                        name = str(item.get("name", "")).strip()
                        if not cid:
                            continue
                        parsed.append(
                            {
                                "id": cid,
                                "name": name or cid,
                                "chunkCount": int(item.get("chunkCount", 0) or 0),
                            }
                        )
                self._remote_containers = parsed
                self._append_log("System", f"Container remoti disponibili: {len(parsed)}")
                if parsed:
                    self._open_remote_container_picker()
                return

            if message_type == "status":
                state = str(message.get("state", "processing"))
                if target_session == self.active_session_id:
                    self._append_log("Phone", state)
                else:
                    self._append_log("System", f"[Other chat] {state}")
                lowered = state.strip().lower()
                if message_id and lowered.startswith("canceled"):
                    self._streaming_preview_by_session.pop(target_session, None)
                    self._streaming_thought_by_session.pop(target_session, None)
                    self._clear_pending_request(message_id)
                    if target_session == self.active_session_id:
                        self._render_active_chat()
                return

            if message_type == "partial":
                partial_text = str(message.get("text", ""))
                if partial_text:
                    channel = str(message.get("channel", "answer")).strip().lower()
                    if channel == "thought":
                        self._streaming_thought_by_session[target_session] = partial_text
                    else:
                        self._streaming_preview_by_session[target_session] = partial_text
                    if target_session == self.active_session_id:
                        self._render_active_chat()
                return

            if message_type == "result":
                response_text = str(message.get("text", ""))
                self._streaming_preview_by_session.pop(target_session, None)
                thought_text = str(message.get("thought", "")).strip()
                self._streaming_thought_by_session.pop(target_session, None)
                self.sessions_store.add_message(target_session, "assistant", response_text)
                if thought_text and self.show_thoughts_var.get():
                    self.sessions_store.add_message(target_session, "thought", thought_text)
                if target_session == self.active_session_id:
                    self._render_active_chat()
                else:
                    self._append_log("System", "Response received in another chat tab")
                if message_id:
                    self._clear_pending_request(message_id)
                self._refresh_sessions_list(self.active_session_id)
                self._refresh_memory_label()
                return

            if message_type == "error":
                error_text = str(message.get("error", "Unknown error"))
                self._streaming_preview_by_session.pop(target_session, None)
                self._streaming_thought_by_session.pop(target_session, None)
                self.sessions_store.add_message(target_session, "error", error_text)
                if target_session == self.active_session_id:
                    self._append_log("Phone error", error_text)
                else:
                    self._append_log("System", "Phone error received in another chat tab")
                if message_id:
                    self._clear_pending_request(message_id)
                self._refresh_sessions_list(self.active_session_id)
                return

            self._append_log("Phone", str(message))
            return

        if event_type == "quick_send":
            text = str(event.get("text", "")).strip()
            if not text:
                return
            self.prompt_entry.delete("1.0", tk.END)
            self.prompt_entry.insert("1.0", text)
            if self.connected:
                self.on_send()
            else:
                self._append_log("System", "Quick request copied into composer (bridge not connected)")
            return

    def _adjust_input_height(self, _event=None) -> None:
        try:
            content = self.prompt_entry.get("1.0", "end-1c")
            lines = content.count("\n") + 1
            width_chars = self.prompt_entry.winfo_width() // 8
            if width_chars > 0:
                for line in content.split("\n"):
                    lines += len(line) // width_chars
            target_lines = max(2, min(7, lines))
            new_height = 60 + (target_lines - 2) * 20
            self.prompt_entry.configure(height=new_height)
        except Exception:
            pass

    def _force_app_visibility(self) -> None:
        if self.root.state() == 'withdrawn' or self.root.state() == 'iconic':
            self.root.deiconify()
        self.root.lift()
        if self._is_macos and NSApplication is not None:
            try:
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:
                pass
        if not self.pip_enabled.get():
            self.pip_enabled.set(True)
            self._toggle_pip()

    def _toggle_app_visibility(self) -> None:
        if self.root.state() == 'withdrawn' or self.root.state() == 'iconic':
            self.root.deiconify()
            self.root.lift()
            if self._is_macos and NSApplication is not None:
                try:
                    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
                except Exception:
                    pass
            if not self.pip_enabled.get():
                self.pip_enabled.set(True)
                self._toggle_pip()
        else:
            if self._pip_mode_active:
                self.pip_enabled.set(False)
                self._toggle_pip()
            self.root.iconify()

    def _hide_to_background(self) -> None:
        if self._pip_mode_active:
            self.pip_enabled.set(False)
            self._toggle_pip()
        try:
            self.root.withdraw()
        except Exception:
            self.root.iconify()
        self._append_log("System", "App in background: usa menu bar/shortcut per riaprirla.")

    def on_window_close(self) -> None:
        if self._close_to_background_on_close:
            self._hide_to_background()
            return
        self.on_close()

    def on_close(self) -> None:
        if self._overlay_listener is not None:
            try:
                self._overlay_listener.stop()
            except Exception:
                pass
            self._overlay_listener = None
        self._stop_menu_bar_icon()
        latest_settings = self._load_settings()
        latest_settings["overlay_bg_color"] = self._overlay_bg_color
        latest_settings["overlay_width"] = self._overlay_width
        latest_settings["overlay_height"] = self._overlay_height
        latest_settings["overlay_resizable"] = self._overlay_resizable
        latest_settings["auto_connect_on_start"] = self._auto_connect_on_start
        latest_settings["auto_retry_known_device"] = self._auto_retry_known_device
        latest_settings["auto_check_updates"] = self._auto_check_updates
        latest_settings["close_to_background_on_close"] = self._close_to_background_on_close
        latest_settings["menu_bar_mode_enabled"] = self._menu_bar_mode_enabled
        latest_settings["hide_dock_icon_enabled"] = self._hide_dock_icon_enabled
        latest_settings["last_connected_address"] = self._last_connected_address
        self._save_settings(latest_settings)
        self._hide_overlay_window()
        self.client.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    DesktopChatApp().run()
