package com.example.geminibridge

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.util.Locale
import java.util.UUID

class SettingsRepository(context: Context) {
    private val prefs: SharedPreferences = createPreferences(context.applicationContext)

    fun getApiKey(): String = prefs.getString(KEY_API_KEY, "") ?: ""

    fun setApiKey(value: String) {
        prefs.edit().putString(KEY_API_KEY, value.trim()).apply()
    }

    fun getModel(): String = prefs.getString(KEY_MODEL, DEFAULT_MODEL) ?: DEFAULT_MODEL

    fun setModel(value: String) {
        val model = value.trim().ifEmpty { DEFAULT_MODEL }
        prefs.edit().putString(KEY_MODEL, model).apply()
    }

    fun getOrCreateBridgeId(): String {
        val existing = normalizeBridgeId(prefs.getString(KEY_BRIDGE_ID, "") ?: "")
        if (existing.isNotEmpty()) {
            return existing
        }
        val generated = normalizeBridgeId(
            UUID.randomUUID().toString().replace("-", "").uppercase(Locale.US).take(12)
        )
        prefs.edit().putString(KEY_BRIDGE_ID, generated).apply()
        return generated
    }

    private fun normalizeBridgeId(raw: String): String {
        val cleaned = raw.trim().uppercase(Locale.US).replace("[^0-9A-F]".toRegex(), "")
        if (cleaned.isEmpty()) {
            return ""
        }
        if (cleaned.length >= 12) {
            return cleaned.take(12)
        }
        return cleaned.padEnd(12, '0')
    }

    private fun createPreferences(context: Context): SharedPreferences {
        return try {
            val masterKey = MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()

            EncryptedSharedPreferences.create(
                context,
                "gemini_bridge_secure_prefs",
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            )
        } catch (_: Throwable) {
            context.getSharedPreferences("gemini_bridge_fallback_prefs", Context.MODE_PRIVATE)
        }
    }

    companion object {
        private const val KEY_API_KEY = "api_key"
        private const val KEY_MODEL = "model"
        private const val KEY_BRIDGE_ID = "bridge_id"
        const val DEFAULT_MODEL = "gemini-2.0-flash"
    }
}
