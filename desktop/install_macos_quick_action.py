#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape


SERVICE_NAME = "Ask Gemini BLE"
BUNDLE_ID = "com.mnbrain.automator.askgeminible"


def _workflow_xml(command_path: Path) -> str:
    command = f'exec "/bin/zsh" "{command_path}"'
    command_xml = escape(command)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>AMApplicationBuild</key>
  <string>523</string>
  <key>AMApplicationVersion</key>
  <string>2.10</string>
  <key>AMDocumentVersion</key>
  <string>2</string>
  <key>actions</key>
  <array>
    <dict>
      <key>action</key>
      <dict>
        <key>AMAccepts</key>
        <dict>
          <key>Container</key>
          <string>List</string>
          <key>Optional</key>
          <true/>
          <key>Types</key>
          <array>
            <string>com.apple.cocoa.string</string>
          </array>
        </dict>
        <key>AMActionVersion</key>
        <string>2.0.3</string>
        <key>AMApplication</key>
        <array>
          <string>Automator</string>
        </array>
        <key>AMParameterProperties</key>
        <dict>
          <key>COMMAND_STRING</key>
          <dict/>
          <key>CheckedForUserDefaultShell</key>
          <dict/>
          <key>inputMethod</key>
          <dict/>
          <key>shell</key>
          <dict/>
          <key>source</key>
          <dict/>
        </dict>
        <key>AMProvides</key>
        <dict>
          <key>Container</key>
          <string>List</string>
          <key>Types</key>
          <array>
            <string>com.apple.cocoa.string</string>
          </array>
        </dict>
        <key>ActionBundlePath</key>
        <string>/System/Library/Automator/Run Shell Script.action</string>
        <key>ActionName</key>
        <string>Run Shell Script</string>
        <key>ActionParameters</key>
        <dict>
          <key>COMMAND_STRING</key>
          <string>{command_xml}</string>
          <key>CheckedForUserDefaultShell</key>
          <true/>
          <key>inputMethod</key>
          <integer>0</integer>
          <key>shell</key>
          <string>/bin/zsh</string>
          <key>source</key>
          <string></string>
        </dict>
        <key>BundleIdentifier</key>
        <string>com.apple.RunShellScript</string>
        <key>CFBundleVersion</key>
        <string>2.0.3</string>
        <key>CanShowSelectedItemsWhenRun</key>
        <false/>
        <key>CanShowWhenRun</key>
        <true/>
        <key>Category</key>
        <array>
          <string>AMCategoryUtilities</string>
        </array>
        <key>Class Name</key>
        <string>RunShellScriptAction</string>
        <key>InputUUID</key>
        <string>983B20A7-C9A8-4C4A-9A75-7E1AF95C0001</string>
        <key>Keywords</key>
        <array>
          <string>Shell</string>
          <string>Script</string>
          <string>Command</string>
          <string>Run</string>
          <string>Unix</string>
        </array>
        <key>OutputUUID</key>
        <string>983B20A7-C9A8-4C4A-9A75-7E1AF95C0002</string>
        <key>UUID</key>
        <string>983B20A7-C9A8-4C4A-9A75-7E1AF95C0003</string>
        <key>UnlocalizedApplications</key>
        <array>
          <string>Automator</string>
        </array>
        <key>arguments</key>
        <dict>
          <key>0</key>
          <dict>
            <key>default value</key>
            <integer>0</integer>
            <key>name</key>
            <string>inputMethod</string>
            <key>required</key>
            <string>0</string>
            <key>type</key>
            <string>0</string>
            <key>uuid</key>
            <string>0</string>
          </dict>
          <key>1</key>
          <dict>
            <key>default value</key>
            <string></string>
            <key>name</key>
            <string>source</string>
            <key>required</key>
            <string>0</string>
            <key>type</key>
            <string>0</string>
            <key>uuid</key>
            <string>1</string>
          </dict>
          <key>2</key>
          <dict>
            <key>default value</key>
            <false/>
            <key>name</key>
            <string>CheckedForUserDefaultShell</string>
            <key>required</key>
            <string>0</string>
            <key>type</key>
            <string>0</string>
            <key>uuid</key>
            <string>2</string>
          </dict>
          <key>3</key>
          <dict>
            <key>default value</key>
            <string></string>
            <key>name</key>
            <string>COMMAND_STRING</string>
            <key>required</key>
            <string>0</string>
            <key>type</key>
            <string>0</string>
            <key>uuid</key>
            <string>3</string>
          </dict>
          <key>4</key>
          <dict>
            <key>default value</key>
            <string>/bin/zsh</string>
            <key>name</key>
            <string>shell</string>
            <key>required</key>
            <string>0</string>
            <key>type</key>
            <string>0</string>
            <key>uuid</key>
            <string>4</string>
          </dict>
        </dict>
        <key>isViewVisible</key>
        <true/>
        <key>location</key>
        <string>309.000000:316.000000</string>
        <key>nibPath</key>
        <string>/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib</string>
      </dict>
      <key>isViewVisible</key>
      <true/>
    </dict>
  </array>
  <key>connectors</key>
  <dict/>
  <key>workflowMetaData</key>
  <dict>
    <key>serviceInputTypeIdentifier</key>
    <string>com.apple.Automator.text</string>
    <key>serviceOutputTypeIdentifier</key>
    <string>com.apple.Automator.nothing</string>
    <key>serviceProcessesInput</key>
    <integer>0</integer>
    <key>workflowTypeIdentifier</key>
    <string>com.apple.Automator.servicesMenu</string>
  </dict>
</dict>
</plist>
"""


def _info_plist_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>NSServices</key>
  <array>
    <dict>
      <key>NSMenuItem</key>
      <dict>
        <key>default</key>
        <string>{SERVICE_NAME}</string>
      </dict>
      <key>NSMessage</key>
      <string>runWorkflowAsService</string>
      <key>NSSendTypes</key>
      <array>
        <string>public.utf8-plain-text</string>
      </array>
    </dict>
  </array>
  <key>CFBundleIdentifier</key>
  <string>{BUNDLE_ID}</string>
  <key>CFBundleName</key>
  <string>{SERVICE_NAME}</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1.0</string>
</dict>
</plist>
"""


def install(verbose: bool = False) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("Quick Action installer is only supported on macOS")

    base_dir = Path(__file__).resolve().parent
    script_path = (base_dir / "macos_quick_ask.sh").resolve()
    if not script_path.exists():
        raise RuntimeError(f"Script not found: {script_path}")

    wrapper_path = _ensure_wrapper(script_path)

    services_dir = Path.home() / "Library" / "Services"
    workflow_dir = services_dir / f"{SERVICE_NAME}.workflow"
    contents_dir = workflow_dir / "Contents"
    contents_dir.mkdir(parents=True, exist_ok=True)

    info_plist = contents_dir / "Info.plist"
    document_wflow = contents_dir / "document.wflow"

    info_plist.write_text(_info_plist_xml(), encoding="utf-8")
    document_wflow.write_text(_workflow_xml(wrapper_path), encoding="utf-8")

    os.chmod(script_path, 0o755)

    subprocess.run(["plutil", "-lint", str(info_plist)], check=False, capture_output=not verbose)
    subprocess.run(["plutil", "-lint", str(document_wflow)], check=False, capture_output=not verbose)

    subprocess.run(["/System/Library/CoreServices/pbs", "-flush"], check=False, capture_output=not verbose)
    subprocess.run(["killall", "-u", os.environ.get("USER", ""), "pbs"], check=False, capture_output=not verbose)

    return workflow_dir


def _ensure_wrapper(script_path: Path) -> Path:
    runtime_dir = Path.home() / ".gemini_ble"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_script = runtime_dir / "macos_quick_ask.sh"
    runtime_helper = runtime_dir / "macos_quick_ask.py"
    default_wrapper = runtime_dir / "ask_gemini_ble.sh"
    shot_wrapper = runtime_dir / "ask_gemini_ble_shot.sh"
    clipboard_wrapper = runtime_dir / "ask_gemini_ble_clipboard.sh"
    toggle_wrapper = runtime_dir / "toggle_gemini_ble.sh"
    helper_path = script_path.with_name("macos_quick_ask.py")
    inbox_path = runtime_dir / "quick_inbox.jsonl"

    shutil.copy2(script_path, runtime_script)
    shutil.copy2(helper_path, runtime_helper)
    os.chmod(runtime_script, 0o755)
    os.chmod(runtime_helper, 0o755)

    _write_wrapper(default_wrapper, f'exec "{runtime_script}" "$@"')
    _write_wrapper(
        shot_wrapper,
        f'GEMINI_INPUT_TEXT="" exec python3 "{runtime_helper}" "{inbox_path}" quick_overlay "$*"',
    )
    _write_wrapper(
        clipboard_wrapper,
        f'GEMINI_INPUT_TEXT="" exec python3 "{runtime_helper}" "{inbox_path}" quick_clipboard "$*"',
    )
    _write_wrapper(
        toggle_wrapper,
        f'GEMINI_INPUT_TEXT="" exec python3 "{runtime_helper}" "{inbox_path}" toggle_visibility ""',
    )
    return default_wrapper


def _write_wrapper(target_path: Path, command: str) -> None:
    content = (
        "#!/bin/zsh\n"
        "set -euo pipefail\n"
        f"{command}\n"
    )
    target_path.write_text(content, encoding="utf-8")
    os.chmod(target_path, 0o755)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Ask Gemini BLE Quick Action on macOS")
    parser.add_argument("--quiet", action="store_true", help="Suppress console output")
    args = parser.parse_args()

    try:
        workflow_dir = install(verbose=not args.quiet)
    except Exception as exc:
        if not args.quiet:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"Installed Quick Action at: {workflow_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
