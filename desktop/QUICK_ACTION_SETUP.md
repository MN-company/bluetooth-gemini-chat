# macOS Quick Action (Tasto Destro -> Ask Gemini BLE)

Questo setup ti permette di inviare testo selezionato da qualsiasi app alla chat desktop.
Adesso l'app prova a installare automaticamente la Quick Action all'avvio.

## Installazione automatica consigliata

1. Apri la chat desktop.
2. Premi `Install Right-click`.
3. Attendi 2-3 secondi e prova tasto destro -> `Quick Actions` -> `Ask Gemini BLE`.

## Installazione manuale (fallback)

## 1) Rendi eseguibile lo script

```bash
cd /Users/mnbrain/Documents/New\ project/bluetooth-gemini-chat/desktop
chmod +x macos_quick_ask.sh
python3 install_macos_quick_action.py
```

## 2) Crea una Quick Action in Automator

1. Apri `Automator`.
2. Nuovo documento -> `Quick Action`.
3. In alto imposta:
   - `Workflow receives current`: `text`
   - `in`: `any application`
4. Aggiungi azione `Run Shell Script`.
5. Imposta:
   - Shell: `/bin/zsh`
   - Pass input: `to stdin`
6. Script:

```zsh
exec /bin/zsh /Users/mnbrain/.gemini_ble/ask_gemini_ble.sh
```

7. Salva con nome, per esempio: `Ask Gemini BLE`.
8. Se non la vedi al tasto destro, chiudi/riapri l'app sorgente oppure fai logout/login macOS.

## 3) Uso

1. Tieni aperta la chat desktop.
2. Seleziona testo in qualsiasi app.
3. Tasto destro -> `Quick Actions` -> `Ask Gemini BLE`.

Il testo viene inviato subito alla chat se il bridge BLE e connesso.
Se non e connesso, viene inserito nel composer della chat.
