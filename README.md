# Bluetooth Gemini Chat

Chat PC <-> Android via BLE (senza Wi-Fi sul PC), con Gemini sul telefono.

## Cosa include
- Desktop app (Python/Tkinter) con chat multipla, PDF, immagini, markdown, streaming.
- Bridge Android BLE + chiamata Gemini API.
- Selettore modello da PC (`phone-default` o override per richiesta).
- Lista modelli disponibili da API direttamente nell'app Android.
- APK pronta in `dist/app-debug.apk`.

## Installazione rapida (altri dispositivi)

### 1) Android (APK)
1. Abilita `USB debugging` sul telefono.
2. Collega via USB.
3. Installa:
   ```bash
   ./scripts/install_android_apk.sh
   ```
4. Apri app `Gemini Bridge`, inserisci API key, salva, concedi permessi BLE.
5. Premi `Disable Battery Optimization` nell'app Android (consigliato).

### 2) Desktop
```bash
./scripts/setup_desktop.sh
./scripts/run_desktop.sh
```

## Uso minimo
1. Avvia app Android (bridge attivo).
2. Avvia app desktop.
3. `Scan` -> seleziona telefono -> `Connect`.
4. Scrivi prompt e `Send`.

## Script utili
- `scripts/setup_desktop.sh`: crea venv e installa dipendenze desktop.
- `scripts/run_desktop.sh`: avvia la chat desktop.
- `scripts/install_android_apk.sh`: installa APK via adb.
- `scripts/build_android_apk.sh`: rebuild APK debug e copia in `dist/`.

## Dove sono i file principali
- Desktop UI: `desktop/app.py`
- BLE desktop: `desktop/ble_client.py`
- Android service: `android/GeminiBluetoothBridge/app/src/main/java/com/example/geminibridge/BleKeepAliveService.kt`
- Gemini client Android: `android/GeminiBluetoothBridge/app/src/main/java/com/example/geminibridge/GeminiApiClient.kt`

## Note
- Quota e billing sono della Gemini API key/progetto, non del piano consumer Gemini app.
- Su telefoni Xiaomi/MIUI/HyperOS: disattivare ottimizzazione batteria è fondamentale.
