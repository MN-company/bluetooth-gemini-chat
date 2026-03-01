from __future__ import annotations

import json
import os
import platform
import queue
import re
import subprocess
import tempfile
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, simpledialog, ttk
from typing import Any

from ble_client import BleChatClient
from chat_sessions import ChatSessionsStore
from pdf_context import PdfContextEngine

try:
    from PIL import ImageGrab
except Exception:
    ImageGrab = None

MODEL_PRESETS = [
    "phone-default",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]


class DesktopChatApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Gemini BLE Chat")
        self.root.geometry("1240x780")
        self.root.minsize(1020, 640)

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

        self._configure_theme()
        self._build_ui()
        self._refresh_sessions_list(self.active_session_id)
        self._render_active_chat()
        self._refresh_memory_label()
        self._refresh_context_preview()
        self._auto_install_quick_action()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll_events)

    def _configure_theme(self) -> None:
        self.root.configure(bg="#eef3fb")
        self.root.option_add("*Font", "Avenir 12")

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("App.TFrame", background="#eef3fb")
        style.configure("Header.TFrame", background="#eef3fb")
        style.configure("Card.TFrame", background="#ffffff", relief=tk.FLAT)
        style.configure("Title.TLabel", background="#eef3fb", foreground="#14233d", font=("Avenir", 19, "bold"))
        style.configure("Subtitle.TLabel", background="#eef3fb", foreground="#5a667c", font=("Avenir", 11))
        style.configure("Status.TLabel", background="#eef3fb", foreground="#1d2f50", font=("Avenir", 11, "bold"))
        style.configure("Section.TLabel", background="#ffffff", foreground="#20314f", font=("Avenir", 12, "bold"))
        style.configure("Meta.TLabel", background="#ffffff", foreground="#616d80", font=("Avenir", 10))
        style.configure("Action.TButton", padding=(10, 7))

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, style="App.TFrame", padding=(16, 14))
        main.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(main, style="Header.TFrame")
        header.pack(fill=tk.X)

        title_col = ttk.Frame(header, style="Header.TFrame")
        title_col.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(title_col, text="Gemini over Bluetooth", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            title_col,
            text="Multi-chat history, PDF/Image context, optional Google Search grounding",
            style="Subtitle.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))

        status_col = ttk.Frame(header, style="Header.TFrame")
        status_col.pack(side=tk.RIGHT, anchor=tk.NE)
        self.status_var = tk.StringVar(value="Not connected")
        self.link_var = tk.StringVar(value="Link: n/a")
        ttk.Label(status_col, textvariable=self.status_var, style="Status.TLabel").pack(anchor=tk.E)
        ttk.Label(status_col, textvariable=self.link_var, style="Subtitle.TLabel").pack(anchor=tk.E, pady=(2, 0))

        body = ttk.Frame(main, style="App.TFrame")
        body.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        sidebar = ttk.Frame(body, style="Card.TFrame", padding=12, width=390)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        ttk.Label(sidebar, text="Bridge", style="Section.TLabel").pack(anchor=tk.W)
        self._build_button_grid(
            sidebar,
            [
                ("Scan", self.on_scan),
                ("Connect", self.on_connect),
                ("Disconnect", self.on_disconnect),
            ],
            columns=3,
            pady=(8, 8),
        )

        self.devices_list = tk.Listbox(
            sidebar,
            height=5,
            bg="#f9fbff",
            fg="#1f2430",
            selectbackground="#d9e7ff",
            activestyle=tk.NONE,
            borderwidth=1,
            relief=tk.SOLID,
            highlightthickness=0,
        )
        self.devices_list.pack(fill=tk.X, expand=False, pady=(0, 10))

        ttk.Label(sidebar, text="Chats", style="Section.TLabel").pack(anchor=tk.W)
        self._build_button_grid(
            sidebar,
            [
                ("New", self.on_new_chat),
                ("Rename", self.on_rename_chat),
                ("Delete", self.on_delete_chat),
            ],
            columns=3,
            pady=(8, 6),
        )

        self.chats_list = tk.Listbox(
            sidebar,
            height=7,
            bg="#f9fbff",
            fg="#1f2430",
            selectbackground="#d9e7ff",
            activestyle=tk.NONE,
            borderwidth=1,
            relief=tk.SOLID,
            highlightthickness=0,
        )
        self.chats_list.pack(fill=tk.X, expand=False, pady=(0, 10))
        self.chats_list.bind("<<ListboxSelect>>", self._on_session_selected)

        ttk.Label(sidebar, text="Context", style="Section.TLabel").pack(anchor=tk.W)
        self._build_button_grid(
            sidebar,
            [
                ("Attach Image", self.on_attach_image),
                ("Add PDF", self.on_add_pdf),
                ("Screenshot", self.on_quick_screenshot),
                ("Ask Clipboard", self.on_clipboard_send),
            ],
            columns=2,
            pady=(8, 6),
        )
        self._build_button_grid(
            sidebar,
            [
                ("Clear Image", self.on_clear_image),
                ("Clear PDFs", self.on_clear_pdfs),
            ],
            columns=2,
            pady=(0, 6),
        )

        ttk.Label(sidebar, text="Runtime", style="Section.TLabel").pack(anchor=tk.W, pady=(4, 0))
        model_row = ttk.Frame(sidebar, style="Card.TFrame")
        model_row.pack(fill=tk.X, pady=(8, 6))
        ttk.Label(model_row, text="Model", style="Meta.TLabel").pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value=MODEL_PRESETS[0])
        self.model_combo = ttk.Combobox(
            model_row,
            textvariable=self.model_var,
            values=MODEL_PRESETS,
            state="readonly",
            width=24,
        )
        self.model_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.model_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_context_preview())
        ttk.Button(
            model_row,
            text="Default",
            style="Action.TButton",
            command=self._set_phone_default_model,
        ).pack(side=tk.LEFT, padx=(6, 0))

        self.web_search_enabled = tk.BooleanVar(value=False)
        web_check = ttk.Checkbutton(
            sidebar,
            text="Enable Web Search (Google)",
            variable=self.web_search_enabled,
            command=self._refresh_context_preview,
        )
        web_check.pack(anchor=tk.W, pady=(0, 6))

        self.thinking_enabled = tk.BooleanVar(value=False)
        thinking_check = ttk.Checkbutton(
            sidebar,
            text="Thinking Mode",
            variable=self.thinking_enabled,
            command=self._refresh_context_preview,
        )
        thinking_check.pack(anchor=tk.W, pady=(0, 4))

        think_row = ttk.Frame(sidebar, style="Card.TFrame")
        think_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(think_row, text="Budget", style="Meta.TLabel").pack(side=tk.LEFT)
        self.thinking_budget_var = tk.StringVar(value="1024")
        self.thinking_budget_spin = tk.Spinbox(
            think_row,
            from_=0,
            to=24576,
            increment=256,
            textvariable=self.thinking_budget_var,
            width=8,
            relief=tk.SOLID,
            borderwidth=1,
            command=self._refresh_context_preview,
        )
        self.thinking_budget_spin.pack(side=tk.LEFT, padx=(6, 0))
        self.thinking_budget_spin.bind("<FocusOut>", lambda _e: self._refresh_context_preview())
        self.thinking_auto_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            think_row,
            text="Auto",
            variable=self.thinking_auto_var,
            command=self._refresh_context_preview,
        ).pack(side=tk.LEFT, padx=(8, 0))
        self.show_thoughts_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            sidebar,
            text="Show Thought Trace",
            variable=self.show_thoughts_var,
            command=self._refresh_context_preview,
        ).pack(anchor=tk.W, pady=(0, 6))

        self.image_var = tk.StringVar(value="Image: none")
        self.pdf_var = tk.StringVar(value="PDF: none")
        self.memory_var = tk.StringVar(value="")
        ttk.Label(sidebar, textvariable=self.image_var, style="Meta.TLabel").pack(anchor=tk.W, pady=(2, 0))
        ttk.Label(sidebar, textvariable=self.pdf_var, style="Meta.TLabel").pack(anchor=tk.W, pady=(2, 0))
        ttk.Label(sidebar, textvariable=self.memory_var, style="Meta.TLabel").pack(anchor=tk.W, pady=(2, 0))

        self._build_button_grid(
            sidebar,
            [
                ("Clear Memory", self.on_clear_memory),
                ("Install Right-click", self.on_install_quick_action),
            ],
            columns=2,
            pady=(10, 0),
        )

        right = ttk.Frame(body, style="App.TFrame")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))

        chat_card = ttk.Frame(right, style="Card.TFrame", padding=(8, 8))
        chat_card.pack(fill=tk.BOTH, expand=True)

        self.chat_log = tk.Text(
            chat_card,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg="#ffffff",
            fg="#1f2430",
            insertbackground="#1f2430",
            borderwidth=0,
            highlightthickness=0,
            padx=10,
            pady=10,
        )
        self.chat_log.pack(fill=tk.BOTH, expand=True)
        self._configure_chat_tags()

        composer = ttk.Frame(right, style="Card.TFrame", padding=(10, 10))
        composer.pack(fill=tk.X, pady=(10, 0))

        self.context_preview_var = tk.StringVar(value="No active attachments")
        ttk.Label(composer, textvariable=self.context_preview_var, style="Meta.TLabel").pack(anchor=tk.W)

        self.prompt_entry = tk.Text(
            composer,
            height=4,
            wrap=tk.WORD,
            bg="#f8faff",
            fg="#1f2430",
            insertbackground="#1f2430",
            borderwidth=1,
            relief=tk.SOLID,
            highlightthickness=0,
            padx=10,
            pady=8,
        )
        self.prompt_entry.pack(fill=tk.X, expand=True, pady=(8, 8))
        self.prompt_entry.bind("<Return>", self._on_prompt_return)
        self.prompt_entry.bind("<Command-BackSpace>", self._on_clear_composer_hotkey)
        self.prompt_entry.bind("<Command-Delete>", self._on_clear_composer_hotkey)

        send_row = ttk.Frame(composer, style="Card.TFrame")
        send_row.pack(fill=tk.X)
        ttk.Label(
            send_row,
            text="Enter = send, Shift+Enter = newline",
            style="Meta.TLabel",
        ).pack(side=tk.LEFT)
        ttk.Button(send_row, text="Send", style="Action.TButton", command=self.on_send).pack(side=tk.RIGHT)

    def _set_phone_default_model(self) -> None:
        self.model_var.set(MODEL_PRESETS[0])
        self._refresh_context_preview()

    def _build_button_grid(
        self,
        parent: tk.Widget,
        entries: list[tuple[str, Any]],
        columns: int = 2,
        pady: tuple[int, int] = (6, 6),
    ) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.pack(fill=tk.X, pady=pady)
        for col in range(columns):
            frame.columnconfigure(col, weight=1, uniform=f"btn-{id(frame)}")

        for idx, (label, command) in enumerate(entries):
            row = idx // columns
            col = idx % columns
            pad_right = 6 if col < (columns - 1) else 0
            pad_bottom = 6 if idx < (len(entries) - columns) else 0
            ttk.Button(frame, text=label, style="Action.TButton", command=command).grid(
                row=row,
                column=col,
                sticky="ew",
                padx=(0, pad_right),
                pady=(0, pad_bottom),
            )

        return frame

    def _configure_chat_tags(self) -> None:
        self.chat_log.tag_configure("role_you", foreground="#1a56d9", font=("Avenir", 10, "bold"), spacing1=10)
        self.chat_log.tag_configure(
            "msg_you",
            foreground="#102a57",
            background="#d7e7ff",
            lmargin1=200,
            lmargin2=200,
            rmargin=14,
            spacing3=8,
        )

        self.chat_log.tag_configure("role_gemini", foreground="#117a45", font=("Avenir", 10, "bold"), spacing1=10)
        self.chat_log.tag_configure(
            "msg_gemini",
            foreground="#1f2430",
            background="#e7f4ea",
            lmargin1=14,
            lmargin2=14,
            rmargin=210,
            spacing3=8,
        )

        self.chat_log.tag_configure("role_thought", foreground="#7d4f00", font=("Avenir", 10, "bold"), spacing1=8)
        self.chat_log.tag_configure(
            "msg_thought",
            foreground="#5f4a16",
            background="#fff5d9",
            lmargin1=14,
            lmargin2=14,
            rmargin=210,
            spacing3=8,
        )

        self.chat_log.tag_configure("role_phone", foreground="#5f52bf", font=("Avenir", 10, "bold"), spacing1=8)
        self.chat_log.tag_configure("msg_phone", foreground="#3d3f50", lmargin1=14, lmargin2=14, rmargin=160)

        self.chat_log.tag_configure("role_system", foreground="#4f5d75", font=("Avenir", 10, "bold"), spacing1=8)
        self.chat_log.tag_configure("msg_system", foreground="#4f5d75", lmargin1=14, lmargin2=14, rmargin=100)

        self.chat_log.tag_configure("role_error", foreground="#d93025", font=("Avenir", 10, "bold"), spacing1=8)
        self.chat_log.tag_configure("msg_error", foreground="#d93025", lmargin1=14, lmargin2=14, rmargin=100)
        self.chat_log.tag_configure("md_h1", font=("Avenir", 14, "bold"), spacing1=10)
        self.chat_log.tag_configure("md_h2", font=("Avenir", 13, "bold"), spacing1=8)
        self.chat_log.tag_configure("md_h3", font=("Avenir", 12, "bold"), spacing1=6)
        self.chat_log.tag_configure("md_bold", font=("Avenir", 11, "bold"))
        self.chat_log.tag_configure("md_inline_code", font=("Menlo", 10), background="#f1f5fb", foreground="#21405f")
        self.chat_log.tag_configure("md_code_block", font=("Menlo", 10), background="#f7f9fc", foreground="#1f2430")
        self.chat_log.tag_configure("md_link", foreground="#1a73e8", underline=True)

    def _on_prompt_return(self, event: tk.Event[tk.Text]) -> str | None:
        # Shift+Enter inserts newline; Enter sends.
        if event.state & 0x0001:
            return None
        self.on_send()
        return "break"

    def _on_clear_composer_hotkey(self, _: tk.Event[tk.Text]) -> str:
        self.prompt_entry.delete("1.0", tk.END)
        return "break"

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

        self.chat_log.config(state=tk.NORMAL)
        self.chat_log.insert(tk.END, f"{role}\n", role_tag)
        if role_key in {"gemini", "assistant"}:
            self._insert_markdown_message(clean, msg_tag)
            self.chat_log.insert(tk.END, "\n", msg_tag)
        else:
            self.chat_log.insert(tk.END, f"{clean}\n\n", msg_tag)
        self.chat_log.see(tk.END)
        self.chat_log.config(state=tk.DISABLED)

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
        self.chat_log.tag_bind(tag_name, "<Button-1>", lambda _e, t=tag_name: self._open_md_link(t))
        self.chat_log.tag_bind(tag_name, "<Enter>", lambda _e: self.chat_log.config(cursor="hand2"))
        self.chat_log.tag_bind(tag_name, "<Leave>", lambda _e: self.chat_log.config(cursor="xterm"))

    def _open_md_link(self, tag_name: str) -> None:
        url = self._md_link_urls.get(tag_name)
        if not url:
            return
        try:
            webbrowser.open(url, new=2)
        except Exception:
            self._append_log("System", f"Open link manually: {url}")

    def _clear_chat_widget(self) -> None:
        self.chat_log.config(state=tk.NORMAL)
        self.chat_log.delete("1.0", tk.END)
        self.chat_log.config(state=tk.DISABLED)

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
        for idx, session in enumerate(sessions):
            title = str(session["title"])
            count = int(session["messageCount"])
            self.chats_list.insert(tk.END, f"{title} ({count})")
            self._session_ids_in_view.append(session["id"])
            if session["id"] == target:
                selected_idx = idx
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
        system_name = platform.system().lower()
        if system_name == "darwin":
            tmp = tempfile.NamedTemporaryFile(prefix="gemini-shot-", suffix=".png", delete=False)
            path = tmp.name
            tmp.close()
            try:
                # Avoid pre-existing empty file edge cases for screencapture.
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
                    if "cancel" in lowered:
                        self._append_log("System", "Screenshot canceled")
                    elif "not authorized" in lowered or "permission" in lowered:
                        self._append_log(
                            "Error",
                            "Screenshot blocked: abilita Screen Recording per Terminal/Python in macOS Settings.",
                        )
                    elif "could not create image from rect" in lowered:
                        # Some macOS builds fail writing file for region capture; fallback to clipboard capture.
                        clip_result = subprocess.run(
                            ["screencapture", "-i", "-c"],
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                        clip_err = (clip_result.stderr or "").strip().lower()
                        if clip_result.returncode == 0 and ImageGrab is not None:
                            try:
                                clip_img = ImageGrab.grabclipboard()  # type: ignore[union-attr]
                                if clip_img is not None:
                                    clip_img.save(path, format="PNG")
                                    self._set_selected_image(path)
                                    return
                            except Exception:
                                pass
                        if "cancel" in clip_err:
                            self._append_log("System", "Screenshot canceled")
                        else:
                            self._append_log(
                                "Error",
                                "Screenshot failed (rect capture error). Verifica i permessi Screen Recording.",
                            )
                    else:
                        detail = stderr if stderr else f"exit code {result.returncode}"
                        self._append_log("Error", f"Screenshot failed: {detail}")
                    try:
                        Path(path).unlink(missing_ok=True)
                    except OSError:
                        pass
                    return
                if not Path(path).exists() or Path(path).stat().st_size <= 0:
                    self._append_log(
                        "Error",
                        "Screenshot non disponibile: nessun file creato. Controlla permessi Screen Recording.",
                    )
                    return
                self._set_selected_image(path)
            except Exception as exc:
                self._append_log("Error", f"Screenshot failed: {exc}")
            return

        if ImageGrab is None:
            self._append_log("Error", "Quick screenshot not supported on this OS without Pillow ImageGrab")
            return

        try:
            image = ImageGrab.grab()  # type: ignore[union-attr]
            fd, path = tempfile.mkstemp(prefix="gemini-shot-", suffix=".png")
            os.close(fd)
            image.save(path, format="PNG")
            self._set_selected_image(path)
        except Exception as exc:
            self._append_log("Error", f"Screenshot failed: {exc}")

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
            text = str(payload.get("text", "")).strip()
            if not text:
                continue
            self.events.put({"type": "quick_send", "text": text})

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

        context_blocks: list[dict[str, Any]] = []
        if self.selected_pdf_paths:
            try:
                context_blocks = self.pdf_context_engine.build_context(prompt, self.selected_pdf_paths)
            except ValueError as exc:
                self._append_log("Error", str(exc))
                return

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
        model_override = selected_model if selected_model and selected_model != MODEL_PRESETS[0] else None

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
            )
        except ValueError as exc:
            self._append_log("Error", str(exc))
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

    def _poll_events(self) -> None:
        self._consume_quick_inbox()

        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)

        self.root.after(100, self._poll_events)

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")

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
            if isinstance(percent, int) and isinstance(current, int) and isinstance(total, int):
                self.status_var.set(f"Sending payload... {percent}% ({current}/{total} packets)")
            return

        if event_type == "sent":
            if self.connected:
                self.status_var.set("Connected")
            return

        if event_type == "incoming":
            message = event.get("message", {})
            message_type = message.get("type")
            message_id = str(message.get("messageId", "")).strip()
            target_session = self._pending_request_session.get(message_id, self.active_session_id)

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

    def on_close(self) -> None:
        self.client.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    DesktopChatApp().run()
