# macOS Quick Action + Apple Shortcuts

All'avvio, l'app desktop installa automaticamente la Quick Action di testo e crea questi wrapper:

- `~/.gemini_ble/ask_gemini_ble.sh` -> invia testo selezionato
- `~/.gemini_ble/ask_gemini_ble_shot.sh` -> trigger screenshot+ask overlay
- `~/.gemini_ble/toggle_gemini_ble.sh` -> mostra/nasconde finestra chat

Tutti gli eventi rapidi passano da `~/.gemini_ble/quick_inbox.jsonl` (runtime condiviso, indipendente dalla cartella progetto).

## Quick Action (tasto destro testo)

1. Tieni aperta la chat desktop.
2. Seleziona testo in qualsiasi app.
3. Tasto destro -> `Quick Actions` -> `Ask Gemini BLE`.

## Apple Shortcuts (consigliato per hotkey macOS)

1. Apri `Shortcuts` e crea un nuovo shortcut.
2. Aggiungi `Run Shell Script` con comando:
   ```zsh
   /Users/$USER/.gemini_ble/ask_gemini_ble_shot.sh
   ```
3. Assegna scorciatoia tastiera (es. `Cmd+Shift+G`).

Opzionale: crea un secondo shortcut per toggle finestra con:

```zsh
/Users/$USER/.gemini_ble/toggle_gemini_ble.sh
```
