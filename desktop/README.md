# Desktop BLE Chat Client

## Avvio

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Uso

1. `Scan` per trovare dispositivi BLE.
2. Seleziona il telefono Android con servizio bridge attivo.
3. `Connect`.
4. (Opzionale) `Attach Image` per allegare un'immagine.
5. (Opzionale) `Screenshot` per catturare rapidamente e allegare.
6. (Opzionale) `Ask Clipboard` per inviare testo/immagine dagli appunti.
7. (Opzionale) `Add PDF` per aggiungere documenti usati come contesto.
8. (Opzionale) abilita `Enable Web Search (Google)` per grounding web.
9. (Opzionale) abilita `Thinking Mode` e imposta `Budget` (o `Auto`) + `Show Thought Trace`.
10. Usa `New/Rename/Delete` per chat multiple con cronologia persistente.
11. Scrivi prompt e premi `Send`.
12. Su macOS puoi usare `cmd + backspace` nel composer per pulirlo.
13. Overlay screenshot rapido:
   - macOS: usa Apple Shortcuts + `~/.gemini_ble/ask_gemini_ble_shot.sh` (puoi assegnare `Cmd+Shift+G`)
   - Windows: `Ctrl+Shift+G` (hotkey globale integrata)
   Esegue screenshot area, invia direttamente a Gemini e mostra risposta in overlay semitrasparente.

## Dettagli

- Usa `bleak` per connessione BLE.
- Usa `tkinter` per UI chat locale.
- Implementa framing/chunking binario in `ble_protocol.py`.
- Supporta richieste multimodali (testo + immagine) con limite immagine lato desktop.
- Limite immagine corrente: circa 140 KB (target compressione ~56 KB).
- Se Pillow e disponibile, il client comprime/scala automaticamente immagini grandi per invio piu rapido.
- Supporta contesto PDF con estrazione locale (pypdf) e retrieval di chunk rilevanti per domanda.
- Per query PDF generiche (es. "di cosa parla il PDF?") usa fallback automatico sui chunk introduttivi.
- Supporta chat multiple con cronologia persistente (file `chat_sessions.json`).
- Rendering markdown nelle risposte Gemini (heading, code block, link cliccabili).
- Heartbeat ping/pong e auto-reconnect BLE per mantenere la sessione stabile.
- Mostra stima token prima invio e warning su richieste a rischio timeout.
- Mostra progress upload su payload BLE grandi (es. immagini).
- Supporta streaming progressivo delle risposte Gemini anche con web search (fallback automatici).
- Supporta Thinking Mode + Think Budget + thought trace (`includeThoughts`) nel payload API.
- Supporta quick action/shortcuts macOS via script `macos_quick_ask.sh` (vedi `QUICK_ACTION_SETUP.md`).
- Tenta install automatica della Quick Action macOS all'avvio; bottone `Install Right-click` per reinstall.
- Supporta hotkey globale via `pynput` su Windows/Linux; su macOS usa Apple Shortcuts.
- Su macOS lo screenshot richiede permesso `Screen Recording` per Terminal/Python.
