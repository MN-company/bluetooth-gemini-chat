from __future__ import annotations

import json
import os
import platform
import queue
import re
import subprocess
import tempfile
import tkinter as tk
import customtkinter as ctk
import webbrowser
from pathlib import Path
from tkinter import filedialog, simpledialog, ttk
from typing import Any
from tkinterdnd2 import TkinterDnD, DND_FILES

class CTkinterDnD(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)


from ble_client import BleChatClient
from chat_sessions import ChatSessionsStore
from context_store import ContextStore
from pdf_context import PdfContextEngine

try:
    from PIL import ImageGrab
except Exception:
    ImageGrab = None

try:
    from pynput import keyboard as pynput_keyboard
except Exception:
    pynput_keyboard = None

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


class DesktopChatApp:
    def __init__(self) -> None:
        self.root = CTkinterDnD()
        self.root.title("Gemini BLE Chat")
        self.root.geometry("1240x780")
        self.root.minsize(300, 400)
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind('<<Drop>>', self._on_file_drop)

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
        self._streaming_preview_by_session: dict[str, str] = {}
        self._streaming_thought_by_session: dict[str, str] = {}
        self._session_ids_in_view: list[str] = []
        self._last_link_state = "unknown"
        self._quick_inbox_path = Path(__file__).with_name("quick_inbox.jsonl")
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
        self._overlay_hide_after_id: str | None = None
        self._overlay_window: tk.Toplevel | None = None
        self._overlay_text_var = tk.StringVar(value="")
        self._toggle_flag_path = Path(__file__).with_name("toggle.flag")
        self._toggle_flag_mtime = 0.0
        self._clipboard_flag_path = Path(__file__).with_name("clipboard.flag")
        self._clipboard_flag_mtime = 0.0

        self._settings_path = Path(__file__).with_name("settings.json")
        _saved = self._load_settings()
        self.system_instructions_var = tk.StringVar(value=_saved.get("system_instructions", ""))
        self.pinned_pdf_paths: list[str] = _saved.get("pinned_pdf_paths", [])

        self._context_store = ContextStore(Path(__file__).parent)
        self._active_container_id: str | None = None
        self._selected_container_idx: int | None = None
        self._container_transfer_request_id: str | None = None
        self._transfer_dialog: ctk.CTkToplevel | None = None
        self._transfer_progress_var: tk.DoubleVar | None = None
        self._transfer_label_var: tk.StringVar | None = None
        self._transfer_started_time: float = 0.0

        self._configure_theme()
        self._build_ui()
        self._refresh_sessions_list(self.active_session_id)
        self._render_active_chat()
        self._refresh_memory_label()
        self._refresh_context_preview()
        self._auto_install_quick_action()
        self._start_overlay_hotkey_listener()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll_events)

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

    def _save_settings(self, data: dict[str, Any]) -> None:
        try:
            with self._settings_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def on_open_settings(self) -> None:
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Settings")
        dialog.geometry("540x540")
        dialog.transient(self.root)
        dialog.grab_set()

        # --- Section 1: System Instructions ---
        ctk.CTkLabel(dialog, text="System Instructions (Global Prompt):", font=("Avenir", 14, "bold")).pack(pady=(12, 4), padx=12, anchor=tk.W)
        ctk.CTkLabel(dialog, text="Iniettate silenziosamente in ogni richiesta.", font=("Avenir", 11), text_color="#888888").pack(padx=12, anchor=tk.W)

        textbox = ctk.CTkTextbox(dialog, height=120, font=("Avenir", 13), fg_color="#1e1e1e", border_width=1, border_color="#333333")
        textbox.pack(fill=tk.X, padx=12, pady=(6, 12))
        textbox.insert("1.0", self.system_instructions_var.get())

        # --- Section 2: Pinned PDFs ---
        ctk.CTkLabel(dialog, text="📚 Documenti Fissi (PDF sempre attivi):", font=("Avenir", 14, "bold")).pack(pady=(4, 4), padx=12, anchor=tk.W)
        ctk.CTkLabel(dialog, text="Allegati automaticamente ad ogni messaggio senza doverli ricaricare.", font=("Avenir", 11), text_color="#888888").pack(padx=12, anchor=tk.W)

        pinned_list_var = tk.Variable(value=list(self.pinned_pdf_paths))
        pdf_listbox = tk.Listbox(
            dialog,
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

        pdf_btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        pdf_btn_row.pack(fill=tk.X, padx=12, pady=(0, 12))
        ctk.CTkButton(pdf_btn_row, text="+ Aggiungi PDF", command=add_pdf, width=120, fg_color="#1f538d").pack(side=tk.LEFT, padx=(0, 8))
        ctk.CTkButton(pdf_btn_row, text="− Rimuovi selezionato", command=remove_pdf, width=160, fg_color="transparent", border_width=1, hover_color="#333333").pack(side=tk.LEFT)

        def save() -> None:
            text = textbox.get("1.0", tk.END).strip()
            self.system_instructions_var.set(text)
            self.pinned_pdf_paths = list(pinned_list_var.get())
            old_settings = self._load_settings()
            old_settings["system_instructions"] = text
            old_settings["pinned_pdf_paths"] = self.pinned_pdf_paths
            self._save_settings(old_settings)
            dialog.destroy()
            n = len(self.pinned_pdf_paths)
            self._append_log("System", f"Settings saved. Pinned PDFs: {n}. System instructions: {'YES' if text else 'none'}.")

        ctk.CTkButton(dialog, text="💾 SALVA", command=save, fg_color="#1f538d").pack(pady=(0, 14))

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
            self._kb_active_label.configure(text="nessun container attivo", text_color="#555555")

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
            self._open_transfer_dialog(c.name, size_kb)
        except ValueError as exc:
            self._append_log("Error", str(exc))

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
            self._close_transfer_dialog(success=True)

    def _close_transfer_dialog(self, success: bool = True) -> None:
        d = self._transfer_dialog
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
            # Auto-activate the container that was just uploaded
            cid = self._selected_container_id()
            if cid:
                self._active_container_id = cid
                c = self._context_store.get(cid)
                self._append_log("System", f"Container auto-attivato: {c.name if c else cid}")
                self._refresh_container_list()


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

    def _show_overlay_message(self, text: str, ttl_ms: int = 12000) -> None:
        clean = text.strip()
        if not clean:
            return

        if self._overlay_window is None or not self._overlay_window.winfo_exists():
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-alpha", 0.5)
            except Exception:
                pass
            win.configure(bg="#0f172a")

            width, height = 460, 220
            x = max(12, win.winfo_screenwidth() - width - 18)
            y = max(12, win.winfo_screenheight() - height - 40)
            win.geometry(f"{width}x{height}+{x}+{y}")

            frame = tk.Frame(win, bg="#0f172a", padx=12, pady=10)
            frame.pack(fill=tk.BOTH, expand=True)
            tk.Label(
                frame,
                text="Gemini Quick Reply",
                bg="#0f172a",
                fg="#dbeafe",
                font=("Avenir", 11, "bold"),
                anchor="w",
            ).pack(fill=tk.X)
            tk.Message(
                frame,
                textvariable=self._overlay_text_var,
                bg="#0f172a",
                fg="#f8fafc",
                font=("Avenir", 11),
                width=430,
                anchor="w",
                justify=tk.LEFT,
            ).pack(fill=tk.BOTH, expand=True, pady=(8, 0))

            self._overlay_window = win

        self._overlay_text_var.set(clean[:1800])
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
        if win is not None and win.winfo_exists():
            win.destroy()

    def _cleanup_overlay_request(self, request_id: str) -> None:
        self._overlay_request_ids.discard(request_id)
        path = self._overlay_image_paths_by_request.pop(request_id, None)
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

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
                                "Screenshot blocked: abilita Screen Recording per Terminal/Python in macOS Settings.",
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

    def on_scan(self) -> None:
        self.devices_list.delete(0, tk.END)
        self.devices = []
        self.client.scan_devices()

    def on_connect(self) -> None:
        selected = self.devices_list.curselection()
        if not selected:
            self._append_log("System", "Select a device first")
            return

        idx = selected[0]
        device = self.devices[idx]
        self.client.connect(device["address"])

    def on_disconnect(self) -> None:
        self.client.disconnect()

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

    def on_hotkey_overlay_triggered(self, prompt_override: str | None = None) -> None:
        if not self.connected:
            self._show_overlay_message("Bridge non connesso", ttl_ms=3500)
            return

        path = self._capture_area_screenshot_path(log_errors=False)
        if path is None:
            self._show_overlay_message("Screenshot annullato", ttl_ms=2500)
            return

        selected_model = self.model_var.get().strip() if hasattr(self, "model_var") else MODEL_PRESETS[0]
        model_override = selected_model if selected_model and selected_model != MODEL_PRESETS[0] else None
        thinking_budget = self._get_thinking_budget()
        thinking_enabled = self.thinking_enabled.get()
        include_thoughts = thinking_enabled and self.show_thoughts_var.get()

        prompt = (
            prompt_override.strip()
            if prompt_override and prompt_override.strip()
            else "Analizza rapidamente questo screenshot. Rispondi in italiano con massimo 5 righe."
        )
        try:
            request_id = self.client.send_prompt(
                prompt,
                model=model_override,
                image_path=path,
                enable_web_search=self.web_search_enabled.get(),
                thinking_enabled=thinking_enabled,
                thinking_budget=thinking_budget,
                include_thoughts=include_thoughts,
            )
        except Exception as exc:
            self._show_overlay_message(f"Invio screenshot fallito: {exc}", ttl_ms=5000)
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
            return

        self._overlay_request_ids.add(request_id)
        self._overlay_image_paths_by_request[request_id] = path
        self._show_overlay_message("Analisi screenshot in corso...", ttl_ms=18000)

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

        if ImageGrab is None:
            self._append_log("System", "Clipboard empty or unsupported format")
            return

        try:
            image = ImageGrab.grabclipboard()  # type: ignore[union-attr]
            if image is None:
                self._append_log("System", "Clipboard empty or unsupported format")
                return
            fd, path = tempfile.mkstemp(prefix="gemini-clip-", suffix=".png")
            os.close(fd)
            image.save(path, format="PNG")
            self._set_selected_image(path)
            self.prompt_entry.delete("1.0", tk.END)
            self.prompt_entry.insert("1.0", "Descrivi questo screenshot.")
            self.on_send()
        except Exception as exc:
            self._append_log("Error", f"Clipboard import failed: {exc}")

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

        session_id = self.active_session_id
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

        self._pending_request_session[request_id] = session_id
        self._refresh_memory_label()

        self.prompt_entry.delete("1.0", tk.END)
        self.on_clear_image()

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

        if event_type == "status":
            status = event.get("text", "")
            self.status_var.set(status)
            self._append_log("System", status)
            return

        if event_type == "error":
            message = event.get("text", "Unknown error")
            self._append_log("Error", message)
            return

        if event_type == "scan_result":
            self.devices = event.get("devices", [])
            self.devices_list.delete(0, tk.END)
            for device in self.devices:
                label = f"{device['name']} ({device['address']})"
                self.devices_list.insert(tk.END, label)
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
            if message_id and message_id in self._overlay_request_ids:
                if message_type == "partial":
                    channel = str(message.get("channel", "answer")).strip().lower()
                    if channel != "thought":
                        partial_text = str(message.get("text", "")).strip()
                        if partial_text:
                            self._show_overlay_message(partial_text + "\n▌", ttl_ms=12000)
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
                        self._show_overlay_message(state, ttl_ms=3000)
                    return

            target_session = self._pending_request_session.get(message_id, self.active_session_id)

            if message_type == "container_ack":
                chunk_count = message.get("chunkCount", "?")
                self._close_transfer_dialog(success=False)  # dialog already logged via _close
                self._append_log("System", f"📱 Container confermato dal telefono ({chunk_count} chunk salvati).")
                return

            if message_type == "status":
                state = str(message.get("state", "processing"))
                if target_session == self.active_session_id:
                    self._append_log("Phone", state)
                else:
                    self._append_log("System", f"[Other chat] {state}")
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
                    self._pending_request_session.pop(message_id, None)
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
                    self._pending_request_session.pop(message_id, None)
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
        if not self.pip_enabled.get():
            self.pip_enabled.set(True)
            self._toggle_pip()

    def _toggle_app_visibility(self) -> None:
        if self.root.state() == 'withdrawn' or self.root.state() == 'iconic':
            self.root.deiconify()
            self.root.lift()
            if not self.pip_enabled.get():
                self.pip_enabled.set(True)
                self._toggle_pip()
        else:
            if self._pip_mode_active:
                self.pip_enabled.set(False)
                self._toggle_pip()
            self.root.iconify()

    def on_close(self) -> None:
        if self._overlay_listener is not None:
            try:
                self._overlay_listener.stop()
            except Exception:
                pass
            self._overlay_listener = None
        self._hide_overlay_window()
        self.client.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    DesktopChatApp().run()
