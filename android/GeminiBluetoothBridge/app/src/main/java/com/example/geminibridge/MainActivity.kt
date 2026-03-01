package com.example.geminibridge

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        viewModel.updatePermissions(hasAllRequiredPermissions())

        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    BridgeScreen(
                        viewModel = viewModel,
                        onCheckPermissions = { hasAllRequiredPermissions() },
                        onRequestPermissions = { launcher ->
                            launcher.launch(requiredPermissions())
                        },
                        onCheckBatteryOptimizationExempt = { isIgnoringBatteryOptimizations() },
                        onRequestDisableBatteryOptimization = { launcher ->
                            requestDisableBatteryOptimization(launcher)
                        },
                    )
                }
            }
        }
    }

    private fun hasAllRequiredPermissions(): Boolean {
        return requiredPermissions().all { permission ->
            ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED
        }
    }

    private fun requiredPermissions(): Array<String> {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            arrayOf(
                Manifest.permission.BLUETOOTH_CONNECT,
                Manifest.permission.BLUETOOTH_SCAN,
                Manifest.permission.BLUETOOTH_ADVERTISE,
            )
        } else {
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }
    }

    private fun isIgnoringBatteryOptimizations(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return true
        val manager = getSystemService(PowerManager::class.java) ?: return false
        return manager.isIgnoringBatteryOptimizations(packageName)
    }

    private fun requestDisableBatteryOptimization(
        launcher: androidx.activity.result.ActivityResultLauncher<Intent>,
    ) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
            data = Uri.parse("package:$packageName")
        }
        runCatching { launcher.launch(intent) }.onFailure {
            val fallback = Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
            runCatching { launcher.launch(fallback) }
        }
    }
}

@Composable
private fun BridgeScreen(
    viewModel: MainViewModel,
    onCheckPermissions: () -> Boolean,
    onRequestPermissions: (androidx.activity.result.ActivityResultLauncher<Array<String>>) -> Unit,
    onCheckBatteryOptimizationExempt: () -> Boolean,
    onRequestDisableBatteryOptimization: (androidx.activity.result.ActivityResultLauncher<Intent>) -> Unit,
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    var apiKey by remember { mutableStateOf(uiState.apiKey) }
    var model by remember { mutableStateOf(uiState.model) }
    var batteryExempt by remember { mutableStateOf(onCheckBatteryOptimizationExempt()) }

    val permissionLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestMultiplePermissions(),
    ) {
        val granted = onCheckPermissions()
        viewModel.updatePermissions(granted)
    }
    val batteryOptimizationLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        contract = ActivityResultContracts.StartActivityForResult(),
    ) {
        batteryExempt = onCheckBatteryOptimizationExempt()
    }

    LaunchedEffect(uiState.apiKey) {
        apiKey = uiState.apiKey
    }
    LaunchedEffect(uiState.model) {
        model = uiState.model
    }
    LaunchedEffect(uiState.apiKey) {
        if (uiState.apiKey.isNotBlank()) {
            viewModel.loadAvailableModels(uiState.apiKey)
        }
    }

    LaunchedEffect(uiState.permissionsGranted, uiState.serviceRunning) {
        batteryExempt = onCheckBatteryOptimizationExempt()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.Top,
    ) {
        Text(
            text = "Gemini Bluetooth Bridge",
            style = MaterialTheme.typography.headlineSmall,
        )

        Spacer(modifier = Modifier.height(8.dp))

        Text(
            text = "Status: ${uiState.bridgeStatus}",
            style = MaterialTheme.typography.bodyMedium,
        )

        Spacer(modifier = Modifier.height(4.dp))

        Text(
            text = if (uiState.serviceRunning) "Service: running" else "Service: stopped",
            style = MaterialTheme.typography.bodySmall,
        )

        Spacer(modifier = Modifier.height(12.dp))

        if (!uiState.permissionsGranted) {
            Button(onClick = { onRequestPermissions(permissionLauncher) }) {
                Text("Grant Bluetooth Permissions")
            }
            Spacer(modifier = Modifier.height(16.dp))
        }

        Text(
            text = if (batteryExempt) {
                "Battery optimization: disabled for app"
            } else {
                "Battery optimization: enabled (may break background BLE)"
            },
            style = MaterialTheme.typography.bodySmall,
        )

        if (!batteryExempt) {
            Spacer(modifier = Modifier.height(6.dp))
            Button(
                onClick = {
                    onRequestDisableBatteryOptimization(batteryOptimizationLauncher)
                },
            ) {
                Text("Disable Battery Optimization")
            }
            Spacer(modifier = Modifier.height(10.dp))
        } else {
            Spacer(modifier = Modifier.height(12.dp))
        }

        OutlinedTextField(
            value = apiKey,
            onValueChange = { apiKey = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Gemini API Key") },
            visualTransformation = PasswordVisualTransformation(),
            singleLine = true,
        )

        Spacer(modifier = Modifier.height(8.dp))

        OutlinedTextField(
            value = model,
            onValueChange = { model = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Gemini Model") },
            singleLine = true,
        )

        Spacer(modifier = Modifier.height(8.dp))

        Row(modifier = Modifier.fillMaxWidth()) {
            Button(
                onClick = {
                    viewModel.saveSettings(apiKey = apiKey, model = model)
                    viewModel.updatePermissions(onCheckPermissions())
                },
            ) {
                Text("Save Settings")
            }

            Spacer(modifier = Modifier.width(8.dp))

            Button(onClick = { viewModel.restartBridgeService() }) {
                Text("Restart Bridge")
            }

            Spacer(modifier = Modifier.width(8.dp))

            Button(onClick = { viewModel.loadAvailableModels(apiKey) }) {
                Text("Refresh Models")
            }
        }

        if (uiState.modelsLoading) {
            Spacer(modifier = Modifier.height(8.dp))
            Text("Loading models...", style = MaterialTheme.typography.bodySmall)
        } else if (uiState.modelsError.isNotBlank()) {
            Spacer(modifier = Modifier.height(8.dp))
            Text(uiState.modelsError, style = MaterialTheme.typography.bodySmall)
        }

        if (uiState.availableModels.isNotEmpty()) {
            Spacer(modifier = Modifier.height(8.dp))
            Text("Available models", style = MaterialTheme.typography.titleSmall)
            LazyColumn(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(130.dp),
            ) {
                items(uiState.availableModels) { name ->
                    TextButton(
                        onClick = { model = name },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text(name)
                    }
                }
            }
        }

        Spacer(modifier = Modifier.height(16.dp))

        Text("Logs", style = MaterialTheme.typography.titleMedium)
        Spacer(modifier = Modifier.height(8.dp))

        LazyColumn(modifier = Modifier.fillMaxSize()) {
            items(uiState.logs) { line ->
                Text(text = line, style = MaterialTheme.typography.bodySmall)
                Spacer(modifier = Modifier.height(4.dp))
            }
        }
    }
}
