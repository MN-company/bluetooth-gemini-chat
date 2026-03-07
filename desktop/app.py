from __future__ import annotations

import atexit
import json
import os
import platform
import queue
import shlex
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, simpledialog
from tkinter import font as tkfont
from typing import Any

import customtkinter as ctk

from ble_client import BleChatClient

try:
    from PIL import Image as PILImage
    from PIL import ImageDraw
    from PIL import ImageGrab
except Exception:
    PILImage = None
    ImageDraw = None
    ImageGrab = None

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
    from pynput import keyboard as pynput_keyboard
except Exception:
    pynput_keyboard = None


APP_VERSION = "0.2.2"

MODEL_PRESETS = [
    "phone-default",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-pro-preview-03-25",
    "gemini-2.5-flash-preview-04-17",
]

OVERLAY_POSITIONS = {
    "top-right": "Top Right",
    "top-left": "Top Left",
    "bottom-right": "Bottom Right",
    "bottom-left": "Bottom Left",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "system_instruction": "",
    "model": "phone-default",
    "overlay_position": "bottom-right",
    "overlay_text_color": "#F8FAFC",
    "overlay_opacity": 0.72,
    "overlay_text_size": 22,
    "overlay_timeout_seconds": 15,
    "auto_connect_on_start": True,
    "auto_retry_known_device": True,
    "menu_bar_mode_enabled": True,
    "hide_dock_icon_enabled": True,
    "last_connected_address": "",
    "last_connected_bridge_id": "",
    "last_selected_device": "",
}

DEFAULT_SCREENSHOT_PROMPT = (
    "Analizza rapidamente questo screenshot. Rispondi in italiano in modo utile, sintetico e concreto."
)
DEFAULT_CLIPBOARD_PROMPT = (
    "Analizza rapidamente questo contenuto dagli appunti. Rispondi in italiano in modo utile, sintetico e concreto."
)
DEFAULT_QUICK_TEXT_PROMPT = (
    "Analizza rapidamente questo testo. Rispondi in italiano in modo utile, sintetico e concreto."
)
FAST_SCREENSHOT_TARGET_BYTES = 38 * 1024
FAST_SCREENSHOT_MAX_DIMENSION = 640
FAST_CLIPBOARD_TARGET_BYTES = 32 * 1024
FAST_CLIPBOARD_MAX_DIMENSION = 560
OVERLAY_HARD_TIMEOUT_SECONDS = 95.0
OVERLAY_IDLE_TIMEOUT_SECONDS = 24.0
POLL_INTERVAL_MS = 90


if objc is not None and NSObject is not None:
    class _MacMenuActionTarget(NSObject):
        def initWithCallback_(self, callback: Any) -> Any:
            self = objc.super(_MacMenuActionTarget, self).init()
            if self is None:
                return None
            self._callback = callback
            return self

        def onAction_(self, _sender: Any) -> None:
            callback = getattr(self, "_callback", None)
            if callback is None:
                return
            try:
                callback()
            except Exception:
                pass
else:
    _MacMenuActionTarget = None


class DesktopOverlayApp:
    def __init__(self) -> None:
        self._closing_app = False
        self._window_visible = True
        self._platform_name = platform.system().lower()
        self._is_macos = self._platform_name == "darwin"
        self._is_windows = self._platform_name.startswith("windows")
        self._runtime_bridge_dir = Path.home() / ".gemini_ble"
        self._runtime_bridge_dir.mkdir(parents=True, exist_ok=True)
        self._settings_path = Path(__file__).with_name("settings.json")
        self._first_run = not self._settings_path.exists()
        self._settings = self._load_settings()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.title("Gemini BLE Overlay")
        self.root.geometry("680x820")
        self.root.minsize(560, 620)

        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.client = BleChatClient(self.events.put)
        self.client.start()

        self.devices: list[dict[str, str]] = []
        self._device_label_map: dict[str, dict[str, str]] = {}
        self.connected = False
        self._current_link_rtt: int | None = None
        self._last_link_state = "offline"
        self._last_connected_address = str(self._settings.get("last_connected_address", "")).strip() or None
        self._last_connected_bridge_id = self._normalize_bridge_id(self._settings.get("last_connected_bridge_id"))
        self._pending_quick_action: dict[str, Any] | None = None

        self._quick_inbox_path = self._runtime_bridge_dir / "quick_inbox.jsonl"
        self._quick_inbox_offset = 0
        self._toggle_flag_path = self._runtime_bridge_dir / "toggle.flag"
        self._toggle_flag_mtime = 0.0
        self._clipboard_flag_path = self._runtime_bridge_dir / "clipboard.flag"
        self._clipboard_flag_mtime = 0.0

        self._overlay_hotkey_listener: Any | None = None
        self._overlay_request_ids: set[str] = set()
        self._overlay_primary_request_id: str | None = None
        self._overlay_image_paths_by_request: dict[str, str] = {}
        self._overlay_started_at: dict[str, float] = {}
        self._overlay_last_update_at: dict[str, float] = {}
        self._overlay_window: tk.Toplevel | None = None
        self._overlay_canvas: tk.Canvas | None = None
        self._overlay_text_id: int | None = None
        self._overlay_hide_after_id: str | None = None
        self._overlay_pending_render_id: str | None = None
        self._overlay_pending_text: str = ""
        self._overlay_pending_ttl_ms: int = 0
        self._overlay_visible = False

        self._tray_icon: Any | None = None
        self._tray_thread: threading.Thread | None = None
        self._mac_status_item: Any | None = None
        self._mac_status_menu: Any | None = None
        self._mac_status_targets: list[Any] = []

        self.status_var = tk.StringVar(value="Disconnected")
        self.link_var = tk.StringVar(value="Link: offline")
        self.model_var = tk.StringVar(value=str(self._settings.get("model", "phone-default")))
        self.device_selection_var = tk.StringVar(value="")
        self.overlay_position_var = tk.StringVar(value=str(self._settings.get("overlay_position", "bottom-right")))
        self.overlay_text_color_var = tk.StringVar(value=str(self._settings.get("overlay_text_color", "#F8FAFC")))
        self.overlay_opacity_var = tk.DoubleVar(value=float(self._settings.get("overlay_opacity", 0.72)))
        self.overlay_text_size_var = tk.IntVar(value=int(self._settings.get("overlay_text_size", 22)))
        self.overlay_timeout_var = tk.IntVar(value=int(self._settings.get("overlay_timeout_seconds", 15)))
        self.auto_connect_var = tk.BooleanVar(value=bool(self._settings.get("auto_connect_on_start", True)))
        self.auto_retry_var = tk.BooleanVar(value=bool(self._settings.get("auto_retry_known_device", True)))

        self._activity_lines: list[str] = []
        self._build_ui()

        self.client.set_auto_reconnect(self.auto_retry_var.get())
        self._start_menu_bar_icon_if_needed()
        self._apply_macos_activation_policy()
        self._auto_install_quick_action()
        self._start_overlay_hotkey_listener()

        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        if self._is_macos:
            try:
                self.root.createcommand("tk::mac::Quit", self.quit_app)
            except Exception:
                pass
        self.root.bind_all("<Command-q>", self._on_app_quit_shortcut, add="+")
        atexit.register(self._atexit_shutdown)
        self.root.after(POLL_INTERVAL_MS, self._poll_events)
        self.root.after(1100, self._maybe_auto_connect_on_start)

        if self._menu_bar_mode_enabled() and not self._first_run:
            self.root.after(250, self.hide_window)

    def _build_ui(self) -> None:
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        outer = ctk.CTkFrame(self.root, fg_color="transparent")
        outer.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(outer, corner_radius=16)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)
        self.scroll_frame = scroll

        header = ctk.CTkFrame(scroll, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 12))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Gemini BLE Overlay", font=("SF Pro Display", 26, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkLabel(
            header,
            text="Background utility for Shot+Ask over Bluetooth",
            text_color="#94A3B8",
            font=("SF Pro Text", 13),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.header_status_label = ctk.CTkLabel(
            header,
            textvariable=self.status_var,
            width=180,
            anchor="e",
            font=("SF Pro Text", 13, "bold"),
        )
        self.header_status_label.grid(row=0, column=1, sticky="e", padx=(12, 0))
        ctk.CTkLabel(
            header,
            textvariable=self.link_var,
            text_color="#A5B4FC",
            font=("SF Pro Text", 12),
        ).grid(row=1, column=1, sticky="e", padx=(12, 0), pady=(4, 0))

        self._build_connection_card(scroll, row=1)
        self._build_prompt_card(scroll, row=2)
        self._build_overlay_card(scroll, row=3)
        self._build_shortcut_card(scroll, row=4)
        self._build_activity_card(scroll, row=5)
        self._build_footer(scroll, row=6)

    def _build_connection_card(self, parent: ctk.CTkScrollableFrame, row: int) -> None:
        card = ctk.CTkFrame(parent, corner_radius=18)
        card.grid(row=row, column=0, sticky="ew", padx=6, pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(card, text="Connection", font=("SF Pro Display", 18, "bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 4)
        )
        hint = "Auto-connect keeps the bridge ready for shortcuts."
        ctk.CTkLabel(card, text=hint, text_color="#94A3B8", font=("SF Pro Text", 12)).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 10)
        )

        self.device_selector = ctk.CTkOptionMenu(
            card,
            variable=self.device_selection_var,
            values=["No scanned device"],
            dynamic_resizing=False,
            width=320,
        )
        self.device_selector.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 10))

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))
        buttons.grid_columnconfigure((0, 1, 2, 3), weight=1)
        ctk.CTkButton(buttons, text="Scan", command=self.on_scan_devices).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(buttons, text="Connect Selected", command=self.on_connect_selected).grid(
            row=0, column=1, sticky="ew", padx=6
        )
        ctk.CTkButton(buttons, text="Connect Last", command=self.on_connect_last).grid(
            row=0, column=2, sticky="ew", padx=6
        )
        ctk.CTkButton(buttons, text="Disconnect", fg_color="#3B1212", hover_color="#5A1B1B", command=self.on_disconnect).grid(
            row=0, column=3, sticky="ew", padx=(6, 0)
        )

        toggles = ctk.CTkFrame(card, fg_color="transparent")
        toggles.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 12))
        toggles.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkSwitch(
            toggles,
            text="Auto-connect on start",
            variable=self.auto_connect_var,
            command=self.on_settings_changed,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkSwitch(
            toggles,
            text="Auto-retry known phone",
            variable=self.auto_retry_var,
            command=self.on_auto_retry_changed,
        ).grid(row=0, column=1, sticky="w")

    def _build_prompt_card(self, parent: ctk.CTkScrollableFrame, row: int) -> None:
        card = ctk.CTkFrame(parent, corner_radius=18)
        card.grid(row=row, column=0, sticky="ew", padx=6, pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="Prompting", font=("SF Pro Display", 18, "bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 8)
        )
        ctk.CTkLabel(card, text="Model", font=("SF Pro Text", 13, "bold")).grid(
            row=1, column=0, sticky="w", padx=16
        )
        self.model_combo = ctk.CTkComboBox(card, values=MODEL_PRESETS, variable=self.model_var)
        self.model_combo.grid(row=2, column=0, sticky="ew", padx=16, pady=(6, 12))
        self.model_combo.bind("<FocusOut>", lambda _event: self.on_settings_changed())

        ctk.CTkLabel(card, text="System Instruction", font=("SF Pro Text", 13, "bold")).grid(
            row=3, column=0, sticky="w", padx=16
        )
        self.system_instruction_text = ctk.CTkTextbox(
            card,
            height=140,
            wrap="word",
            border_width=1,
            border_color="#2A3444",
            fg_color="#0F172A",
        )
        self.system_instruction_text.grid(row=4, column=0, sticky="ew", padx=16, pady=(6, 14))
        system_text = str(self._settings.get("system_instruction", "")).strip()
        if system_text:
            self.system_instruction_text.insert("1.0", system_text)
        self.system_instruction_text.bind("<FocusOut>", lambda _event: self.on_settings_changed())

    def _build_overlay_card(self, parent: ctk.CTkScrollableFrame, row: int) -> None:
        card = ctk.CTkFrame(parent, corner_radius=18)
        card.grid(row=row, column=0, sticky="ew", padx=6, pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="Overlay", font=("SF Pro Display", 18, "bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 8)
        )

        ctk.CTkLabel(card, text="Position", font=("SF Pro Text", 13, "bold")).grid(
            row=1, column=0, sticky="w", padx=16
        )
        self.position_menu = ctk.CTkOptionMenu(
            card,
            variable=self.overlay_position_var,
            values=list(OVERLAY_POSITIONS.keys()),
            command=lambda _value: self.on_settings_changed(),
        )
        self.position_menu.grid(row=2, column=0, sticky="ew", padx=16, pady=(6, 12))

        color_row = ctk.CTkFrame(card, fg_color="transparent")
        color_row.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 12))
        color_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(color_row, text="Text Color", font=("SF Pro Text", 13, "bold")).grid(row=0, column=0, sticky="w")
        self.color_preview = ctk.CTkLabel(
            color_row,
            text=self.overlay_text_color_var.get(),
            text_color=self.overlay_text_color_var.get(),
            font=("SF Pro Text", 13, "bold"),
        )
        self.color_preview.grid(row=0, column=1, sticky="w", padx=(12, 0))
        ctk.CTkButton(color_row, text="Choose", width=90, command=self.on_pick_overlay_color).grid(
            row=0, column=2, sticky="e"
        )

        self._build_slider_row(card, 4, "Opacity", self.overlay_opacity_var, 0.25, 1.0, 0.01, formatter=lambda value: f"{value:.2f}")
        self._build_slider_row(card, 5, "Text Size", self.overlay_text_size_var, 14, 38, 1, formatter=lambda value: f"{int(round(value))} px")
        self._build_slider_row(card, 6, "Hide After", self.overlay_timeout_var, 4, 40, 1, formatter=lambda value: f"{int(round(value))} s")

        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=7, column=0, sticky="ew", padx=16, pady=(6, 14))
        actions.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkButton(actions, text="Preview Text", command=self.on_preview_overlay).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(actions, text="Ask Clipboard", command=self.on_hotkey_clipboard_triggered).grid(
            row=0, column=1, sticky="ew", padx=6
        )
        ctk.CTkButton(actions, text="Hide Overlay", fg_color="#1F2937", hover_color="#334155", command=self.hide_overlay).grid(
            row=0, column=2, sticky="ew", padx=(6, 0)
        )

    def _build_slider_row(
        self,
        parent: ctk.CTkFrame,
        row: int,
        label: str,
        variable: tk.DoubleVar | tk.IntVar,
        minimum: float,
        maximum: float,
        step: float,
        formatter: Any,
    ) -> None:
        wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        wrapper.grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 10))
        wrapper.grid_columnconfigure(0, weight=1)
        wrapper.grid_columnconfigure(1, weight=0)
        value_var = tk.StringVar(value=formatter(variable.get()))
        setattr(self, f"_{label.lower().replace(' ', '_')}_display_var", value_var)
        ctk.CTkLabel(wrapper, text=label, font=("SF Pro Text", 13, "bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(wrapper, textvariable=value_var, text_color="#93C5FD").grid(row=0, column=1, sticky="e")

        def on_change(value: float) -> None:
            if isinstance(variable, tk.IntVar):
                variable.set(int(round(value)))
            else:
                variable.set(round(value, 2))
            value_var.set(formatter(variable.get()))
            self.on_settings_changed()

        slider = ctk.CTkSlider(
            wrapper,
            from_=minimum,
            to=maximum,
            number_of_steps=max(1, int(round((maximum - minimum) / step))),
            command=on_change,
        )
        slider.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        slider.set(variable.get())

    def _build_shortcut_card(self, parent: ctk.CTkScrollableFrame, row: int) -> None:
        card = ctk.CTkFrame(parent, corner_radius=18)
        card.grid(row=row, column=0, sticky="ew", padx=6, pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="Shortcuts", font=("SF Pro Display", 18, "bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 8)
        )

        shortcuts = self._shortcut_lines()
        for idx, line in enumerate(shortcuts, start=1):
            ctk.CTkLabel(card, text=line, text_color="#CBD5E1", anchor="w", justify="left").grid(
                row=idx, column=0, sticky="ew", padx=16, pady=(0, 6)
            )

        if self._is_macos:
            ctk.CTkButton(card, text="Install macOS Quick Action", command=lambda: self.on_install_quick_action(False)).grid(
                row=len(shortcuts) + 1, column=0, sticky="w", padx=16, pady=(4, 14)
            )

    def _build_activity_card(self, parent: ctk.CTkScrollableFrame, row: int) -> None:
        card = ctk.CTkFrame(parent, corner_radius=18)
        card.grid(row=row, column=0, sticky="ew", padx=6, pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="Recent Activity", font=("SF Pro Display", 18, "bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 8)
        )
        self.activity_box = ctk.CTkTextbox(
            card,
            height=170,
            wrap="word",
            state="disabled",
            border_width=1,
            border_color="#273449",
            fg_color="#0B1120",
        )
        self.activity_box.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))

    def _build_footer(self, parent: ctk.CTkScrollableFrame, row: int) -> None:
        footer = ctk.CTkFrame(parent, fg_color="transparent")
        footer.grid(row=row, column=0, sticky="ew", padx=6, pady=(0, 10))
        footer.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkButton(footer, text="Save Settings", command=self.save_settings).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(footer, text="Hide to Background", command=self.hide_window).grid(row=0, column=1, sticky="ew", padx=6)
        ctk.CTkButton(footer, text="Quit", fg_color="#3B1212", hover_color="#5A1B1B", command=self.quit_app).grid(
            row=0, column=2, sticky="ew", padx=(6, 0)
        )

    def _shortcut_lines(self) -> list[str]:
        if self._is_macos:
            return [
                "Shot+Ask: Apple Shortcut -> ~/.gemini_ble/ask_gemini_ble_shot.sh",
                "Clipboard Ask: Apple Shortcut -> ~/.gemini_ble/ask_gemini_ble_clipboard.sh",
                "Hide Overlay: Apple Shortcut -> ~/.gemini_ble/hide_gemini_ble_overlay.sh",
                "Toggle Settings Window: Apple Shortcut -> ~/.gemini_ble/toggle_gemini_ble.sh",
            ]
        return [
            "Shot+Ask: Ctrl+Shift+G",
            "Clipboard Ask: Ctrl+Shift+C",
            "Hide Overlay: Ctrl+Shift+H",
        ]

    def _load_settings(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self._settings_path.exists():
            try:
                raw = json.loads(self._settings_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
            except Exception:
                data = {}
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        merged["overlay_text_color"] = self._normalize_hex_color(merged.get("overlay_text_color"), "#F8FAFC")
        merged["overlay_position"] = str(merged.get("overlay_position", "bottom-right")).strip().lower()
        if merged["overlay_position"] not in OVERLAY_POSITIONS:
            merged["overlay_position"] = "bottom-right"
        merged["overlay_text_size"] = int(max(14, min(38, int(merged.get("overlay_text_size", 22)))))
        merged["overlay_timeout_seconds"] = int(max(4, min(40, int(merged.get("overlay_timeout_seconds", 15)))))
        opacity = float(merged.get("overlay_opacity", 0.72))
        merged["overlay_opacity"] = max(0.25, min(1.0, opacity))
        return merged

    def save_settings(self) -> None:
        model = self.model_var.get().strip() or "phone-default"
        payload = self._load_settings()
        payload.update(
            {
                "system_instruction": self.system_instruction_text.get("1.0", "end-1c").strip(),
                "model": model,
                "overlay_position": self.overlay_position_var.get().strip().lower(),
                "overlay_text_color": self.overlay_text_color_var.get().strip(),
                "overlay_opacity": round(float(self.overlay_opacity_var.get()), 2),
                "overlay_text_size": int(self.overlay_text_size_var.get()),
                "overlay_timeout_seconds": int(self.overlay_timeout_var.get()),
                "auto_connect_on_start": bool(self.auto_connect_var.get()),
                "auto_retry_known_device": bool(self.auto_retry_var.get()),
                "last_connected_address": self._last_connected_address or "",
                "last_connected_bridge_id": self._last_connected_bridge_id or "",
                "last_selected_device": self.device_selection_var.get().strip(),
            }
        )
        self._settings_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._settings = payload
        self.client.set_auto_reconnect(self.auto_retry_var.get())
        self._apply_macos_activation_policy()
        self._refresh_menu_bar_icon()
        self._append_log("System", "Settings saved")

    def on_settings_changed(self) -> None:
        self._update_color_preview()
        self._apply_overlay_preferences()

    def on_auto_retry_changed(self) -> None:
        self.client.set_auto_reconnect(self.auto_retry_var.get())
        self.on_settings_changed()

    def on_pick_overlay_color(self) -> None:
        chosen = colorchooser.askcolor(color=self.overlay_text_color_var.get(), parent=self.root)
        if not chosen or not chosen[1]:
            return
        self.overlay_text_color_var.set(self._normalize_hex_color(chosen[1], "#F8FAFC"))
        self._update_color_preview()
        self._apply_overlay_preferences()

    def on_preview_overlay(self) -> None:
        self._show_overlay_message("Questo e' un test dell'overlay Gemini BLE.", ttl_ms=int(self.overlay_timeout_var.get()) * 1000)

    def _update_color_preview(self) -> None:
        if hasattr(self, "color_preview"):
            color = self._normalize_hex_color(self.overlay_text_color_var.get(), "#F8FAFC")
            self.overlay_text_color_var.set(color)
            self.color_preview.configure(text=color, text_color=color)

    def _menu_bar_mode_enabled(self) -> bool:
        enabled = bool(self._settings.get("menu_bar_mode_enabled", True))
        if self._is_macos:
            return enabled
        return bool(pystray is not None and PILImage is not None and enabled)

    def _hide_dock_icon_enabled(self) -> bool:
        if not self._is_macos:
            return False
        return bool(self._settings.get("hide_dock_icon_enabled", True))

    def _append_log(self, source: str, text: str) -> None:
        line = f"{source}: {text}".strip()
        if not line:
            return
        timestamp = time.strftime("%H:%M:%S")
        rendered = f"[{timestamp}] {line}"
        self._activity_lines.append(rendered)
        self._activity_lines = self._activity_lines[-80:]
        try:
            self.activity_box.configure(state="normal")
            self.activity_box.delete("1.0", tk.END)
            self.activity_box.insert("1.0", "\n".join(self._activity_lines))
            self.activity_box.configure(state="disabled")
            self.activity_box.see(tk.END)
        except Exception:
            pass

    def _enqueue_menu_action(self, action: str, **payload: Any) -> None:
        event: dict[str, Any] = {"type": "menu_action", "action": action}
        event.update(payload)
        try:
            self.events.put_nowait(event)
        except Exception:
            pass

    def _format_device_label(self, device: dict[str, str]) -> str:
        name = str(device.get("name", "Gemini Bridge")).strip() or "Gemini Bridge"
        bridge_id = self._normalize_bridge_id(device.get("bridge_id"))
        suffix = bridge_id or str(device.get("address", "")).strip()
        if suffix:
            return f"{name} • {suffix}"
        return name

    def _refresh_device_selector(self) -> None:
        labels = [self._format_device_label(device) for device in self.devices]
        self._device_label_map = {label: device for label, device in zip(labels, self.devices)}
        if not labels:
            labels = ["No scanned device"]
            self._device_label_map.clear()
        self.device_selector.configure(values=labels)

        current = self.device_selection_var.get().strip()
        if current in self._device_label_map:
            return

        preferred = str(self._settings.get("last_selected_device", "")).strip()
        if preferred in self._device_label_map:
            self.device_selection_var.set(preferred)
            return

        match = self._find_best_label_for_last_device()
        if match:
            self.device_selection_var.set(match)
            return

        self.device_selection_var.set(labels[0])

    def _find_best_label_for_last_device(self) -> str | None:
        for label, device in self._device_label_map.items():
            address = str(device.get("address", "")).strip()
            bridge_id = self._normalize_bridge_id(device.get("bridge_id"))
            if self._last_connected_bridge_id and bridge_id == self._last_connected_bridge_id:
                return label
            if self._last_connected_address and address == self._last_connected_address:
                return label
        return None

    def _selected_device(self) -> dict[str, str] | None:
        label = self.device_selection_var.get().strip()
        return self._device_label_map.get(label)

    def on_scan_devices(self) -> None:
        self._append_log("System", "Scanning BLE devices...")
        self.client.scan_devices()

    def on_connect_selected(self) -> None:
        device = self._selected_device()
        if device is None:
            self._append_log("System", "No scanned device selected")
            return
        address = str(device.get("address", "")).strip()
        bridge_id = self._normalize_bridge_id(device.get("bridge_id"))
        self._connect_with_hint(address, bridge_id)

    def on_connect_last(self) -> None:
        if self._last_connected_address or self._last_connected_bridge_id:
            self._connect_with_hint(self._last_connected_address, self._last_connected_bridge_id)
            return
        self.on_connect_selected()

    def on_disconnect(self) -> None:
        self.client.disconnect()

    def _connect_with_hint(self, address: str | None, bridge_id: str | None) -> None:
        normalized_bridge = self._normalize_bridge_id(bridge_id)
        target = str(address or "").strip()
        if not target and not normalized_bridge:
            self._append_log("System", "No known device to connect")
            return
        self.status_var.set("Connecting...")
        self.link_var.set("Link: probing...")
        self.client.connect(target, bridge_id=normalized_bridge)

    def _maybe_auto_connect_on_start(self) -> None:
        if not self.auto_connect_var.get():
            return
        if not (self._last_connected_address or self._last_connected_bridge_id):
            return
        self._append_log("System", "Auto-connect enabled: trying last phone")
        self.on_connect_last()

    def _compose_prompt_parts(self, base_prompt: str, extra_text: str | None = None) -> tuple[str, list[dict[str, Any]] | None]:
        prompt = base_prompt.strip()
        context_blocks: list[dict[str, Any]] = []
        system_instruction = self.system_instruction_text.get("1.0", "end-1c").strip()
        if system_instruction:
            context_blocks.append({"type": "text", "text": f"System instruction:\n{system_instruction}"})
        if extra_text:
            prompt = f"{prompt}\n\n{extra_text.strip()}"
        return prompt, context_blocks or None

    def _model_override(self) -> str | None:
        model = self.model_var.get().strip()
        if not model or model == "phone-default":
            return None
        return model

    def _queue_pending_action(self, action: dict[str, Any]) -> bool:
        if self.connected:
            return False
        if not (self._last_connected_address or self._last_connected_bridge_id):
            return False
        self._pending_quick_action = action
        self._show_overlay_message("Bridge disconnesso. Mi ricollego al telefono...", ttl_ms=5000)
        self.on_connect_last()
        return True

    def on_hotkey_overlay_triggered(self, prompt_override: str | None = None) -> None:
        prompt_text = (prompt_override or "").strip()
        if not self.connected and self._queue_pending_action({"type": "shot", "prompt": prompt_text}):
            return
        if not self.connected:
            self._show_overlay_message("Bridge non connesso", ttl_ms=3200)
            return

        screenshot_path = self._capture_area_screenshot_path(log_errors=False)
        if screenshot_path is None:
            self._show_overlay_message("Screenshot annullato", ttl_ms=2500)
            return

        base_prompt = prompt_text or DEFAULT_SCREENSHOT_PROMPT
        self._send_overlay_request(
            prompt=base_prompt,
            source="Shot+Ask",
            status_text="Analisi screenshot...",
            image_path=screenshot_path,
            image_target_bytes=FAST_SCREENSHOT_TARGET_BYTES,
            image_max_dimension=FAST_SCREENSHOT_MAX_DIMENSION,
            fail_text="Invio screenshot fallito",
        )

    def on_hotkey_clipboard_triggered(self, prompt_override: str | None = None) -> None:
        prompt_text = (prompt_override or "").strip()
        if not self.connected and self._queue_pending_action({"type": "clipboard", "prompt": prompt_text}):
            return
        if not self.connected:
            self._show_overlay_message("Bridge non connesso", ttl_ms=3200)
            return

        clipboard_text = self._get_clipboard_text()
        if clipboard_text:
            extra = f"Contenuto degli appunti:\n{clipboard_text}"
            self._send_overlay_request(
                prompt=prompt_text or DEFAULT_CLIPBOARD_PROMPT,
                source="Clipboard",
                status_text="Analisi clipboard...",
                image_path=None,
                fail_text="Invio clipboard fallito",
                extra_text=extra,
            )
            return

        clipboard_image = self._capture_clipboard_image_path(log_errors=False)
        if clipboard_image is None:
            self._show_overlay_message("Clipboard vuota o formato non supportato", ttl_ms=3200)
            return

        self._send_overlay_request(
            prompt=prompt_text or DEFAULT_CLIPBOARD_PROMPT,
            source="Clipboard",
            status_text="Analisi clipboard...",
            image_path=clipboard_image,
            image_target_bytes=FAST_CLIPBOARD_TARGET_BYTES,
            image_max_dimension=FAST_CLIPBOARD_MAX_DIMENSION,
            fail_text="Invio clipboard fallito",
        )

    def _send_text_overlay_request(self, text: str, prompt_override: str | None = None) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        if not self.connected and self._queue_pending_action({"type": "text", "prompt": prompt_override or "", "text": cleaned}):
            return
        if not self.connected:
            self._show_overlay_message("Bridge non connesso", ttl_ms=3200)
            return
        self._send_overlay_request(
            prompt=(prompt_override or DEFAULT_QUICK_TEXT_PROMPT),
            source="Quick Ask",
            status_text="Analisi testo...",
            image_path=None,
            fail_text="Invio testo fallito",
            extra_text=f"Testo:\n{cleaned}",
        )

    def _send_overlay_request(
        self,
        prompt: str,
        source: str,
        status_text: str,
        image_path: str | None,
        fail_text: str,
        image_target_bytes: int | None = None,
        image_max_dimension: int | None = None,
        extra_text: str | None = None,
    ) -> None:
        model_override = self._model_override()
        full_prompt, context_blocks = self._compose_prompt_parts(prompt, extra_text=extra_text)
        try:
            request_id = self.client.send_prompt(
                full_prompt,
                model=model_override,
                image_path=image_path,
                image_target_bytes=image_target_bytes,
                image_max_dimension=image_max_dimension,
                context_blocks=context_blocks,
            )
        except Exception as exc:
            if image_path:
                try:
                    Path(image_path).unlink(missing_ok=True)
                except OSError:
                    pass
            self._show_overlay_message(f"{fail_text}: {exc}", ttl_ms=7000)
            self._append_log("Error", f"{source}: {exc}")
            return

        self._overlay_request_ids.add(request_id)
        self._overlay_primary_request_id = request_id
        self._overlay_started_at[request_id] = time.monotonic()
        self._overlay_last_update_at[request_id] = time.monotonic()
        if image_path:
            self._overlay_image_paths_by_request[request_id] = image_path
        self._show_overlay_message(status_text, ttl_ms=0)
        self._append_log("System", f"{source}: request queued ({request_id})")

    def _get_clipboard_text(self) -> str:
        try:
            return self.root.clipboard_get().strip()
        except Exception:
            return ""

    def _capture_area_screenshot_path(self, log_errors: bool = True) -> str | None:
        if self._is_macos:
            return self._capture_macos_screenshot_path(log_errors=log_errors)
        if self._is_windows:
            return self._capture_windows_screenshot_path(log_errors=log_errors)
        return self._capture_linux_screenshot_path(log_errors=log_errors)

    def _capture_macos_screenshot_path(self, log_errors: bool = True) -> str | None:
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
            stderr = (result.stderr or "").strip().lower()
            if result.returncode != 0 or not Path(path).exists():
                Path(path).unlink(missing_ok=True)
                if log_errors:
                    if "cancel" in stderr:
                        self._append_log("System", "Screenshot canceled")
                    else:
                        self._append_log("Error", f"Screenshot failed: {result.stderr or result.returncode}")
                return None
            return path
        except Exception as exc:
            Path(path).unlink(missing_ok=True)
            if log_errors:
                self._append_log("Error", f"Screenshot failed: {exc}")
            return None

    def _capture_windows_screenshot_path(self, log_errors: bool = True) -> str | None:
        if ImageGrab is None:
            if log_errors:
                self._append_log("Error", "Screenshot requires Pillow on Windows")
            return None

        baseline = self._clipboard_image_signature()
        try:
            subprocess.run(["explorer.exe", "ms-screenclip:"], check=False, capture_output=True)
        except Exception as exc:
            if log_errors:
                self._append_log("Error", f"Cannot open Windows snipping overlay: {exc}")
            return None

        deadline = time.monotonic() + 24.0
        while time.monotonic() < deadline:
            time.sleep(0.2)
            image = self._grab_clipboard_image()
            if image is None:
                continue
            signature = self._image_signature(image)
            if signature == baseline:
                continue
            return self._save_pil_image(image, prefix="gemini-shot-")

        if log_errors:
            self._append_log("System", "Screenshot canceled or timed out")
        return None

    def _capture_linux_screenshot_path(self, log_errors: bool = True) -> str | None:
        tmp = tempfile.NamedTemporaryFile(prefix="gemini-shot-", suffix=".png", delete=False)
        path = tmp.name
        tmp.close()
        commands = [
            ["bash", "-lc", f'command -v grim >/dev/null && command -v slurp >/dev/null && grim -g "$(slurp)" {shlex.quote(path)}'],
            ["gnome-screenshot", "-a", "-f", path],
            ["spectacle", "-brno", path],
            ["import", path],
        ]
        for command in commands:
            try:
                Path(path).unlink(missing_ok=True)
                result = subprocess.run(command, check=False, capture_output=True, text=True)
                if result.returncode == 0 and Path(path).exists():
                    return path
            except Exception:
                continue
        Path(path).unlink(missing_ok=True)
        if log_errors:
            self._append_log("Error", "Interactive screenshot is unavailable on this Linux setup")
        return None

    def _capture_clipboard_image_path(self, log_errors: bool = True) -> str | None:
        image = self._grab_clipboard_image()
        if image is None:
            return None
        return self._save_pil_image(image, prefix="gemini-clipboard-")

    def _grab_clipboard_image(self) -> Any | None:
        if ImageGrab is None:
            return None
        try:
            grabbed = ImageGrab.grabclipboard()
        except Exception:
            return None
        if grabbed is None:
            return None
        if isinstance(grabbed, list):
            for item in grabbed:
                item_path = Path(str(item))
                if item_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} and item_path.exists():
                    try:
                        return PILImage.open(item_path)
                    except Exception:
                        continue
            return None
        return grabbed

    def _save_pil_image(self, image: Any, prefix: str) -> str | None:
        if PILImage is None:
            return None
        tmp = tempfile.NamedTemporaryFile(prefix=prefix, suffix=".png", delete=False)
        path = tmp.name
        tmp.close()
        try:
            image.save(path, format="PNG")
            return path
        except Exception:
            Path(path).unlink(missing_ok=True)
            return None

    def _clipboard_image_signature(self) -> str | None:
        image = self._grab_clipboard_image()
        if image is None:
            return None
        return self._image_signature(image)

    def _image_signature(self, image: Any) -> str | None:
        if image is None:
            return None
        try:
            resized = image.copy()
            resized.thumbnail((64, 64))
            return str(hash(resized.tobytes()))
        except Exception:
            return None

    def _show_overlay_message(self, text: str, ttl_ms: int = 0) -> None:
        clean = text.strip()
        if not clean:
            return
        self._overlay_pending_text = clean[:2200]
        self._overlay_pending_ttl_ms = max(0, ttl_ms)
        if self._overlay_pending_render_id is not None:
            return
        self._overlay_pending_render_id = self.root.after(35, self._flush_overlay_message)

    def _flush_overlay_message(self) -> None:
        self._overlay_pending_render_id = None
        text = self._overlay_pending_text
        ttl_ms = self._overlay_pending_ttl_ms
        if not text:
            return
        if self._overlay_window is None or not self._overlay_window.winfo_exists():
            self._create_overlay_window()
        if self._overlay_window is None or self._overlay_canvas is None or self._overlay_text_id is None:
            return

        self._apply_overlay_preferences()
        canvas = self._overlay_canvas
        screen_width = self.root.winfo_screenwidth()
        wrap_width = min(max(int(screen_width * 0.34), 260), 760)
        font = self._overlay_font()
        canvas.itemconfigure(
            self._overlay_text_id,
            text=text,
            fill=self._normalize_hex_color(self.overlay_text_color_var.get(), "#F8FAFC"),
            font=font,
            width=wrap_width,
        )
        canvas.update_idletasks()
        bbox = canvas.bbox(self._overlay_text_id)
        if bbox is None:
            return
        margin = 10
        width = max(120, (bbox[2] - bbox[0]) + margin * 2)
        height = max(60, (bbox[3] - bbox[1]) + margin * 2)
        canvas.configure(width=width, height=height)
        canvas.coords(self._overlay_text_id, margin, margin)
        x, y = self._overlay_geometry(width, height)
        self._overlay_window.geometry(f"{width}x{height}+{x}+{y}")
        self._overlay_window.deiconify()
        self._overlay_window.lift()
        self._overlay_visible = True
        self._overlay_window.update_idletasks()
        if self._overlay_hide_after_id is not None:
            try:
                self.root.after_cancel(self._overlay_hide_after_id)
            except Exception:
                pass
            self._overlay_hide_after_id = None
        if ttl_ms > 0:
            self._overlay_hide_after_id = self.root.after(ttl_ms, self.hide_overlay)

    def _create_overlay_window(self) -> None:
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        background = "#020617"
        transparent_applied = False
        if self._is_macos:
            try:
                window.configure(bg="systemTransparent")
                window.attributes("-transparent", True)
                transparent_applied = True
            except Exception:
                transparent_applied = False
        elif self._is_windows:
            try:
                window.configure(bg=background)
                window.wm_attributes("-transparentcolor", background)
                transparent_applied = True
            except Exception:
                transparent_applied = False
        if not transparent_applied:
            window.configure(bg=background)

        canvas = tk.Canvas(window, highlightthickness=0, bd=0, bg=window.cget("bg"))
        canvas.pack(fill=tk.BOTH, expand=True)
        text_id = canvas.create_text(
            12,
            12,
            anchor="nw",
            justify="left",
            text="",
            fill=self.overlay_text_color_var.get(),
            font=self._overlay_font(),
        )

        self._overlay_window = window
        self._overlay_canvas = canvas
        self._overlay_text_id = text_id
        self._apply_overlay_preferences()

    def _overlay_font(self) -> tuple[str, int, str]:
        family = "SF Pro Display" if self._is_macos else "Segoe UI"
        return (family, int(self.overlay_text_size_var.get()), "bold")

    def _overlay_geometry(self, width: int, height: int) -> tuple[int, int]:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        margin_x = 28
        margin_y = 58 if self._is_macos else 24
        position = self.overlay_position_var.get().strip().lower()
        right = screen_width - width - margin_x
        left = margin_x
        top = margin_y
        bottom = screen_height - height - margin_y
        if position == "top-left":
            return left, top
        if position == "top-right":
            return max(left, right), top
        if position == "bottom-left":
            return left, max(top, bottom)
        return max(left, right), max(top, bottom)

    def _apply_overlay_preferences(self) -> None:
        window = self._overlay_window
        if window is None or not window.winfo_exists():
            return
        try:
            window.attributes("-alpha", max(0.25, min(1.0, float(self.overlay_opacity_var.get()))))
        except Exception:
            pass
        if self._overlay_canvas is not None and self._overlay_text_id is not None:
            self._overlay_canvas.itemconfigure(
                self._overlay_text_id,
                fill=self._normalize_hex_color(self.overlay_text_color_var.get(), "#F8FAFC"),
                font=self._overlay_font(),
            )

    def hide_overlay(self) -> None:
        if self._overlay_hide_after_id is not None:
            try:
                self.root.after_cancel(self._overlay_hide_after_id)
            except Exception:
                pass
            self._overlay_hide_after_id = None
        if self._overlay_pending_render_id is not None:
            try:
                self.root.after_cancel(self._overlay_pending_render_id)
            except Exception:
                pass
            self._overlay_pending_render_id = None
        self._overlay_pending_text = ""
        window = self._overlay_window
        if window is not None and window.winfo_exists():
            try:
                window.withdraw()
            except Exception:
                pass
        self._overlay_visible = False

    def _cleanup_overlay_request(self, request_id: str) -> None:
        self._overlay_request_ids.discard(request_id)
        self._overlay_started_at.pop(request_id, None)
        self._overlay_last_update_at.pop(request_id, None)
        image_path = self._overlay_image_paths_by_request.pop(request_id, None)
        if image_path:
            try:
                Path(image_path).unlink(missing_ok=True)
            except OSError:
                pass
        if self._overlay_primary_request_id == request_id:
            self._overlay_primary_request_id = next(iter(self._overlay_request_ids), None)

    def _cleanup_all_overlay_requests(self) -> None:
        for request_id in list(self._overlay_request_ids):
            self._cleanup_overlay_request(request_id)

    def _check_overlay_request_timeouts(self) -> None:
        now = time.monotonic()
        timed_out: list[str] = []
        for request_id in list(self._overlay_request_ids):
            started_at = self._overlay_started_at.get(request_id, now)
            last_update = self._overlay_last_update_at.get(request_id, started_at)
            total_elapsed = now - started_at
            idle_elapsed = now - last_update
            if total_elapsed >= OVERLAY_HARD_TIMEOUT_SECONDS or idle_elapsed >= OVERLAY_IDLE_TIMEOUT_SECONDS:
                timed_out.append(request_id)
        for request_id in timed_out:
            if request_id == self._overlay_primary_request_id:
                self._show_overlay_message("Timeout: nessuna risposta dal bridge. Riprova.", ttl_ms=7000)
            self._cleanup_overlay_request(request_id)

    def _consume_quick_inbox(self) -> None:
        if not self._quick_inbox_path.exists():
            self._quick_inbox_offset = 0
            return

        try:
            size = self._quick_inbox_path.stat().st_size
            if size < self._quick_inbox_offset:
                self._quick_inbox_offset = 0
        except OSError:
            return

        try:
            with self._quick_inbox_path.open("r", encoding="utf-8") as handle:
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
            event_type = str(payload.get("type", "")).strip().lower()
            if event_type == "quick_overlay":
                self.events.put({"type": "quick_overlay", "prompt": str(payload.get("prompt", "")).strip()})
            elif event_type in {"quick_clipboard_overlay", "quick_clipboard"}:
                self.events.put({"type": "quick_clipboard_overlay", "prompt": str(payload.get("prompt", "")).strip()})
            elif event_type == "quick_send":
                self.events.put({"type": "quick_send", "text": str(payload.get("text", "")).strip()})
            elif event_type == "toggle_visibility":
                self.events.put({"type": "toggle_visibility"})
            elif event_type == "hide_overlay":
                self.events.put({"type": "hide_overlay"})

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
                self.events.put({"type": "quick_clipboard_overlay", "prompt": ""})
        except OSError:
            pass

    def _poll_events(self) -> None:
        if self._closing_app:
            return
        self._consume_quick_inbox()
        self._consume_toggle_flag()
        self._consume_clipboard_flag()
        self._check_overlay_request_timeouts()

        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)

        self.root.after(POLL_INTERVAL_MS, self._poll_events)

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", "")).strip()

        if event_type == "toggle_visibility":
            self.toggle_window_visibility()
            return

        if event_type == "hide_overlay":
            self.hide_overlay()
            return

        if event_type == "quick_overlay":
            self.on_hotkey_overlay_triggered(str(event.get("prompt", "")))
            return

        if event_type == "quick_clipboard_overlay":
            self.on_hotkey_clipboard_triggered(str(event.get("prompt", "")))
            return

        if event_type == "quick_send":
            text = str(event.get("text", "")).strip()
            if text:
                self._send_text_overlay_request(text)
            return

        if event_type == "menu_action":
            action = str(event.get("action", "")).strip().lower()
            if action == "show_window":
                self.show_window()
                return
            if action == "scan_devices":
                self.on_scan_devices()
                return
            if action == "connect_last":
                self.on_connect_last()
                return
            if action == "connect_hint":
                address = str(event.get("address", "")).strip()
                bridge_id = self._normalize_bridge_id(event.get("bridge_id"))
                self._connect_with_hint(address, bridge_id)
                return
            if action == "disconnect":
                self.on_disconnect()
                return
            if action == "shot":
                self.on_hotkey_overlay_triggered()
                return
            if action == "clipboard":
                self.on_hotkey_clipboard_triggered()
                return
            if action == "hide_overlay":
                self.hide_overlay()
                return
            if action == "set_model":
                self._set_model_from_menu(str(event.get("model", "")).strip())
                return
            if action == "quit":
                self.quit_app()
                return

        if event_type == "status":
            text = str(event.get("text", "")).strip()
            if text:
                self.status_var.set(text)
                self._append_log("System", text)
            return

        if event_type == "error":
            message = str(event.get("text", "Unknown error")).strip()
            if message:
                self._append_log("Error", message)
                if self._overlay_primary_request_id:
                    self._show_overlay_message(f"Errore bridge: {message}", ttl_ms=8000)
                    self._cleanup_all_overlay_requests()
            return

        if event_type == "scan_result":
            devices = event.get("devices", [])
            self.devices = [device for device in devices if isinstance(device, dict)]
            self._refresh_device_selector()
            self._refresh_menu_bar_icon()
            if self.devices:
                self._append_log("System", f"Found {len(self.devices)} Gemini bridge device(s)")
            else:
                self._append_log("System", "No Gemini bridge found")
            return

        if event_type == "connected":
            self.connected = True
            address = str(event.get("address", "")).strip()
            bridge_id = self._normalize_bridge_id(event.get("bridge_id"))
            if address:
                self._last_connected_address = address
            if bridge_id:
                self._last_connected_bridge_id = bridge_id
            self.status_var.set(f"Connected: {str(event.get('device', 'phone')).strip()}")
            self.link_var.set("Link: healthy")
            self._last_link_state = "healthy"
            packet = event.get("max_packet_size", "?")
            self._append_log("System", f"Connected, packet size: {packet}")
            self.save_settings()
            self._refresh_menu_bar_icon()
            if self._pending_quick_action is not None:
                action = self._pending_quick_action
                self._pending_quick_action = None
                self.root.after(320, lambda action=action: self._run_pending_action(action))
            return

        if event_type == "disconnected":
            if self.connected:
                self._append_log("System", "Disconnected")
            self.connected = False
            self.status_var.set("Disconnected")
            self.link_var.set("Link: offline")
            self._last_link_state = "offline"
            self._current_link_rtt = None
            self._refresh_menu_bar_icon()
            if self._overlay_primary_request_id:
                self._show_overlay_message("Bridge disconnesso durante la richiesta", ttl_ms=7000)
                self._cleanup_all_overlay_requests()
            return

        if event_type == "link_quality":
            rtt = event.get("rtt_ms")
            if isinstance(rtt, int):
                self._current_link_rtt = rtt
                self.link_var.set(f"Link RTT: {rtt} ms")
            return

        if event_type == "link_status":
            state = str(event.get("state", "unknown")).strip()
            if state != self._last_link_state:
                self._last_link_state = state
                text = str(event.get("text", "")).strip()
                if text:
                    self._append_log("System", text)
            return

        if event_type == "transfer_progress":
            request_id = str(event.get("request_id", "")).strip()
            percent = event.get("percent")
            current = event.get("current_packets")
            total = event.get("total_packets")
            if request_id:
                self._overlay_last_update_at[request_id] = time.monotonic()
            if (
                request_id
                and request_id == self._overlay_primary_request_id
                and isinstance(percent, int)
                and isinstance(current, int)
                and isinstance(total, int)
            ):
                self._show_overlay_message(f"Invio... {percent}% ({current}/{total})", ttl_ms=0)
            return

        if event_type == "sent":
            request_id = str(event.get("request_id", "")).strip()
            if request_id:
                self._overlay_last_update_at[request_id] = time.monotonic()
            if request_id and request_id == self._overlay_primary_request_id:
                self._show_overlay_message("Upload completato, attendo risposta...", ttl_ms=0)
            return

        if event_type == "incoming":
            message = event.get("message", {})
            if not isinstance(message, dict):
                return
            message_type = str(message.get("type", "")).strip().lower()
            message_id = str(message.get("messageId", "")).strip()
            if not message_id and len(self._overlay_request_ids) == 1:
                message_id = next(iter(self._overlay_request_ids))

            if message_id and message_id in self._overlay_request_ids:
                self._overlay_last_update_at[message_id] = time.monotonic()

            if message_type == "status":
                state = str(message.get("state", "processing")).strip()
                if state and message_id == self._overlay_primary_request_id:
                    self._show_overlay_message(state, ttl_ms=0)
                if state.lower().startswith("canceled") and message_id:
                    self._cleanup_overlay_request(message_id)
                return

            if message_type == "partial":
                if message_id != self._overlay_primary_request_id:
                    return
                channel = str(message.get("channel", "answer")).strip().lower()
                if channel == "thought":
                    return
                partial_text = str(message.get("text", "")).strip()
                if partial_text:
                    self._show_overlay_message(partial_text + "\n▌", ttl_ms=0)
                return

            if message_type == "result":
                response_text = str(message.get("text", "")).strip()
                if message_id == self._overlay_primary_request_id and response_text:
                    ttl_ms = int(self.overlay_timeout_var.get()) * 1000
                    self._show_overlay_message(response_text, ttl_ms=ttl_ms)
                if response_text:
                    self._append_log("Gemini", response_text.splitlines()[0][:160])
                if message_id:
                    self._cleanup_overlay_request(message_id)
                return

            if message_type == "error":
                error_text = str(message.get("error", "Unknown error")).strip()
                if message_id == self._overlay_primary_request_id:
                    self._show_overlay_message(f"Errore: {error_text}", ttl_ms=9000)
                self._append_log("Phone", error_text)
                if message_id:
                    self._cleanup_overlay_request(message_id)
                return

    def _run_pending_action(self, action: dict[str, Any]) -> None:
        action_type = str(action.get("type", "")).strip().lower()
        prompt = str(action.get("prompt", "")).strip()
        if action_type == "shot":
            self.on_hotkey_overlay_triggered(prompt)
        elif action_type == "clipboard":
            self.on_hotkey_clipboard_triggered(prompt)
        elif action_type == "text":
            self._send_text_overlay_request(str(action.get("text", "")), prompt_override=prompt)

    def _normalize_hex_color(self, value: Any, fallback: str) -> str:
        text = str(value or "").strip()
        if len(text) == 7 and text.startswith("#"):
            try:
                int(text[1:], 16)
                return text.upper()
            except ValueError:
                return fallback
        return fallback

    def _normalize_bridge_id(self, value: Any) -> str | None:
        if value is None:
            return None
        clean = "".join(ch for ch in str(value).strip().upper() if ch in "0123456789ABCDEF")
        if len(clean) < 6:
            return None
        return clean[:12]

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._window_visible = True
        if self._is_macos and NSApplication is not None:
            try:
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:
                pass

    def hide_window(self) -> None:
        if not self._menu_bar_mode_enabled():
            self.root.iconify()
            self._window_visible = False
            return
        self.root.withdraw()
        self._window_visible = False

    def toggle_window_visibility(self) -> None:
        if self.root.state() == "withdrawn" or self.root.state() == "iconic" or not self._window_visible:
            self.show_window()
        else:
            self.hide_window()

    def _apply_macos_activation_policy(self) -> None:
        if not self._is_macos or NSApplication is None:
            return
        try:
            app = NSApplication.sharedApplication()
            if self._menu_bar_mode_enabled() and self._hide_dock_icon_enabled():
                app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            else:
                app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        except Exception:
            pass

    def _start_menu_bar_icon_if_needed(self) -> None:
        if not self._menu_bar_mode_enabled():
            return
        if self._is_macos:
            self._start_macos_menu_bar_item()
            return
        self._start_windows_tray_icon()

    def _refresh_menu_bar_icon(self) -> None:
        if not self._menu_bar_mode_enabled():
            return
        if self._is_macos:
            self._start_macos_menu_bar_item(rebuild=True)
            return
        self._refresh_windows_tray_menu()

    def _create_menu_bar_icon_image(self) -> Any | None:
        if PILImage is None or ImageDraw is None:
            return None
        image = PILImage.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        circle = "#0EA5E9" if self.connected else "#334155"
        draw.ellipse((8, 8, 56, 56), fill=circle)
        draw.rounded_rectangle((20, 20, 44, 44), radius=9, fill="#F8FAFC")
        draw.rounded_rectangle((24, 24, 40, 40), radius=5, fill=circle)
        return image

    def _build_pystray_model_menu(self) -> Any:
        items = []
        for model in MODEL_PRESETS:
            label = model

            def on_click(_icon: Any, _item: Any, model_name: str = model) -> None:
                self.root.after(0, lambda model_name=model_name: self._set_model_from_menu(model_name))

            items.append(
                pystray.MenuItem(
                    label,
                    on_click,
                    checked=lambda _item, model_name=model: self.model_var.get().strip() == model_name,
                    radio=True,
                )
            )
        return pystray.Menu(*items)

    def _build_pystray_connect_menu(self) -> Any:
        if not self.devices:
            return pystray.Menu(pystray.MenuItem("No scanned device", None, enabled=False))
        items = []
        for device in self.devices[:8]:
            label = self._format_device_label(device)

            def on_click(_icon: Any, _item: Any, device=device) -> None:
                address = str(device.get("address", "")).strip()
                bridge_id = self._normalize_bridge_id(device.get("bridge_id"))
                self.root.after(0, lambda: self._connect_with_hint(address, bridge_id))

            items.append(pystray.MenuItem(label, on_click))
        return pystray.Menu(*items)

    def _build_pystray_menu(self) -> Any:
        status_label = self.status_var.get().strip() or "Disconnected"
        return pystray.Menu(
            pystray.MenuItem(status_label, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show Settings", lambda _icon, _item: self.root.after(0, self.show_window)),
            pystray.MenuItem("Scan Devices", lambda _icon, _item: self.root.after(0, self.on_scan_devices)),
            pystray.MenuItem("Connect Last", lambda _icon, _item: self.root.after(0, self.on_connect_last)),
            pystray.MenuItem("Connect Scanned", self._build_pystray_connect_menu()),
            pystray.MenuItem("Disconnect", lambda _icon, _item: self.root.after(0, self.on_disconnect), enabled=lambda _item: self.connected),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Shot+Ask", lambda _icon, _item: self.root.after(0, self.on_hotkey_overlay_triggered)),
            pystray.MenuItem("Clipboard Ask", lambda _icon, _item: self.root.after(0, self.on_hotkey_clipboard_triggered)),
            pystray.MenuItem("Hide Overlay", lambda _icon, _item: self.root.after(0, self.hide_overlay)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Models", self._build_pystray_model_menu()),
            pystray.MenuItem("Quit", lambda _icon, _item: self.root.after(0, self.quit_app)),
        )

    def _start_windows_tray_icon(self) -> None:
        if pystray is None or PILImage is None:
            return
        if self._tray_icon is not None:
            return
        image = self._create_menu_bar_icon_image()
        if image is None:
            return
        icon = pystray.Icon("gemini_ble_overlay", image, "Gemini BLE Overlay", self._build_pystray_menu())
        self._tray_icon = icon

        def run_icon() -> None:
            try:
                icon.run()
            except Exception:
                pass

        self._tray_thread = threading.Thread(target=run_icon, name="GeminiBleTray", daemon=True)
        self._tray_thread.start()

    def _refresh_windows_tray_menu(self) -> None:
        if self._tray_icon is None:
            self._start_windows_tray_icon()
            return
        image = self._create_menu_bar_icon_image()
        if image is not None:
            self._tray_icon.icon = image
        self._tray_icon.menu = self._build_pystray_menu()
        try:
            self._tray_icon.update_menu()
        except Exception:
            pass

    def _mac_status_title(self) -> str:
        return "GBE" if self.connected else "GB"

    def _start_macos_menu_bar_item(self, rebuild: bool = False) -> None:
        if not self._is_macos:
            return
        if NSStatusBar is None or NSMenu is None or NSMenuItem is None or _MacMenuActionTarget is None:
            return
        if rebuild:
            self._stop_macos_menu_bar_item()
        if self._mac_status_item is not None:
            return

        status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        if status_item is None:
            return
        button = status_item.button()
        if button is not None:
            try:
                image_name = "sparkles" if self.connected else "bolt.horizontal.circle"
                image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(image_name, "Gemini BLE Overlay")
                if image is not None:
                    button.setImage_(image)
                else:
                    button.setTitle_(self._mac_status_title())
            except Exception:
                button.setTitle_(self._mac_status_title())

        menu = NSMenu.alloc().init()
        targets: list[Any] = []

        def add_item(title: str, callback: Any, enabled: bool = True) -> Any:
            target = _MacMenuActionTarget.alloc().initWithCallback_(callback)
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "onAction:", "")
            item.setTarget_(target)
            item.setEnabled_(enabled)
            menu.addItem_(item)
            targets.append(target)
            return item

        status_line = add_item(self.status_var.get().strip() or "Disconnected", lambda: None, enabled=False)
        status_line.setToolTip_(self.link_var.get().strip())
        menu.addItem_(NSMenuItem.separatorItem())
        add_item("Show Settings", lambda: self._enqueue_menu_action("show_window"))
        add_item("Scan Devices", lambda: self._enqueue_menu_action("scan_devices"))
        add_item("Connect Last", lambda: self._enqueue_menu_action("connect_last"))

        connect_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Connect Scanned", None, "")
        connect_submenu = NSMenu.alloc().init()
        if self.devices:
            for device in self.devices[:8]:
                label = self._format_device_label(device)
                address = str(device.get("address", "")).strip()
                bridge_id = self._normalize_bridge_id(device.get("bridge_id"))
                target = _MacMenuActionTarget.alloc().initWithCallback_(
                    lambda address=address, bridge_id=bridge_id: self._enqueue_menu_action(
                        "connect_hint", address=address, bridge_id=bridge_id
                    )
                )
                sub_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, "onAction:", "")
                sub_item.setTarget_(target)
                connect_submenu.addItem_(sub_item)
                targets.append(target)
        else:
            sub_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("No scanned device", None, "")
            sub_item.setEnabled_(False)
            connect_submenu.addItem_(sub_item)
        connect_item.setSubmenu_(connect_submenu)
        menu.addItem_(connect_item)

        add_item("Disconnect", lambda: self._enqueue_menu_action("disconnect"), enabled=self.connected)
        menu.addItem_(NSMenuItem.separatorItem())
        add_item("Shot+Ask", lambda: self._enqueue_menu_action("shot"))
        add_item("Clipboard Ask", lambda: self._enqueue_menu_action("clipboard"))
        add_item("Hide Overlay", lambda: self._enqueue_menu_action("hide_overlay"))
        menu.addItem_(NSMenuItem.separatorItem())

        models_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Model", None, "")
        models_menu = NSMenu.alloc().init()
        for model in MODEL_PRESETS:
            target = _MacMenuActionTarget.alloc().initWithCallback_(
                lambda model_name=model: self._enqueue_menu_action("set_model", model=model_name)
            )
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(model, "onAction:", "")
            item.setTarget_(target)
            if self.model_var.get().strip() == model:
                try:
                    item.setState_(1)
                except Exception:
                    pass
            models_menu.addItem_(item)
            targets.append(target)
        models_item.setSubmenu_(models_menu)
        menu.addItem_(models_item)

        menu.addItem_(NSMenuItem.separatorItem())
        add_item("Quit", lambda: self._enqueue_menu_action("quit"))

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

    def _set_model_from_menu(self, model_name: str) -> None:
        self.model_var.set(model_name)
        self.save_settings()
        self._append_log("System", f"Model set to {model_name}")

    def _start_overlay_hotkey_listener(self) -> None:
        if self._is_macos or pynput_keyboard is None:
            return
        try:
            listener = pynput_keyboard.GlobalHotKeys(
                {
                    "<ctrl>+<shift>+g": lambda: self.root.after(0, self.on_hotkey_overlay_triggered),
                    "<ctrl>+<shift>+c": lambda: self.root.after(0, self.on_hotkey_clipboard_triggered),
                    "<ctrl>+<shift>+h": lambda: self.root.after(0, self.hide_overlay),
                }
            )
            listener.start()
            self._overlay_hotkey_listener = listener
        except Exception as exc:
            self._append_log("System", f"Global hotkeys unavailable: {exc}")

    def _auto_install_quick_action(self) -> None:
        if not self._is_macos:
            return
        self.on_install_quick_action(silent=True)

    def on_install_quick_action(self, silent: bool = False) -> None:
        installer = Path(__file__).with_name("install_macos_quick_action.py")
        if not installer.exists():
            if not silent:
                self._append_log("Error", "Quick Action installer script not found")
            return
        try:
            result = subprocess.run(
                ["python3", str(installer), "--quiet"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                if not silent:
                    self._append_log("System", "macOS Quick Action installed/updated")
                return
            if not silent:
                stderr = (result.stderr or "").strip() or "unknown error"
                self._append_log("Error", f"Quick Action install failed: {stderr}")
        except Exception as exc:
            if not silent:
                self._append_log("Error", f"Quick Action install failed: {exc}")

    def _on_app_quit_shortcut(self, _event: tk.Event[Any] | None = None) -> str:
        self.quit_app()
        return "break"

    def quit_app(self) -> None:
        if self._closing_app:
            return
        self._closing_app = True
        self.save_settings()
        listener = self._overlay_hotkey_listener
        self._overlay_hotkey_listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        self.hide_overlay()
        self._stop_menu_bar_icon()
        try:
            self.client.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _atexit_shutdown(self) -> None:
        if self._closing_app:
            return
        self._closing_app = True
        listener = self._overlay_hotkey_listener
        self._overlay_hotkey_listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        try:
            self._stop_menu_bar_icon()
        except Exception:
            pass
        try:
            self.client.stop()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    DesktopOverlayApp().run()
