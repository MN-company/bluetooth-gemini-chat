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
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle

// ─── Color palette ────────────────────────────────────────────────────────────
private val BgDark    = Color(0xFF0E0E0F)
private val SurfCard  = Color(0xFF1A1A1E)
private val SurfCard2 = Color(0xFF222228)
private val Accent    = Color(0xFF4F8EF7)
private val AccentGrn = Color(0xFF34A853)
private val TextPrim  = Color(0xFFE8E8EA)
private val TextSec   = Color(0xFF8A8A9A)
private val Danger    = Color(0xFFE05555)

private val AppColorScheme = darkColorScheme(
    background       = BgDark,
    surface          = SurfCard,
    primary          = Accent,
    onPrimary        = Color.White,
    onBackground     = TextPrim,
    onSurface        = TextPrim,
    secondary        = AccentGrn,
    onSecondary      = Color.White,
    error            = Danger,
)

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        viewModel.updatePermissions(hasAllRequiredPermissions())
        setContent {
            MaterialTheme(colorScheme = AppColorScheme) {
                Surface(modifier = Modifier.fillMaxSize(), color = BgDark) {
                    BridgeScreen(
                        viewModel = viewModel,
                        onCheckPermissions = { hasAllRequiredPermissions() },
                        onRequestPermissions = { it.launch(requiredPermissions()) },
                        onCheckBatteryOptimizationExempt = { isIgnoringBatteryOptimizations() },
                        onRequestDisableBatteryOptimization = { requestDisableBatteryOptimization(it) },
                    )
                }
            }
        }
    }

    private fun hasAllRequiredPermissions() =
        requiredPermissions().all { ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED }

    private fun requiredPermissions(): Array<String> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
            arrayOf(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_ADVERTISE)
        else
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)

    private fun isIgnoringBatteryOptimizations(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return true
        return (getSystemService(PowerManager::class.java))?.isIgnoringBatteryOptimizations(packageName) == true
    }

    private fun requestDisableBatteryOptimization(launcher: androidx.activity.result.ActivityResultLauncher<Intent>) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply { data = Uri.parse("package:$packageName") }
        runCatching { launcher.launch(intent) }.onFailure {
            runCatching { launcher.launch(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }
        }
    }
}

// ─── Tabs ─────────────────────────────────────────────────────────────────────

private enum class Tab(val label: String, val icon: ImageVector) {
    SETTINGS("Settings", Icons.Default.Settings),
    CONTAINERS("Libreria", Icons.Default.List),
    LOGS("Logs", Icons.Default.Info),
}

// ─── Main screen ──────────────────────────────────────────────────────────────

@Composable
private fun BridgeScreen(
    viewModel: MainViewModel,
    onCheckPermissions: () -> Boolean,
    onRequestPermissions: (androidx.activity.result.ActivityResultLauncher<Array<String>>) -> Unit,
    onCheckBatteryOptimizationExempt: () -> Boolean,
    onRequestDisableBatteryOptimization: (androidx.activity.result.ActivityResultLauncher<Intent>) -> Unit,
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    var selectedTab by remember { mutableStateOf(Tab.SETTINGS) }
    var apiKey by remember { mutableStateOf(uiState.apiKey) }
    var model  by remember { mutableStateOf(uiState.model) }
    var batteryExempt by remember { mutableStateOf(onCheckBatteryOptimizationExempt()) }

    val permissionLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { viewModel.updatePermissions(onCheckPermissions()) }

    val batteryLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { batteryExempt = onCheckBatteryOptimizationExempt() }

    LaunchedEffect(uiState.apiKey) { apiKey = uiState.apiKey }
    LaunchedEffect(uiState.model) { model = uiState.model }
    LaunchedEffect(uiState.apiKey) { if (uiState.apiKey.isNotBlank()) viewModel.loadAvailableModels(uiState.apiKey) }
    LaunchedEffect(uiState.permissionsGranted, uiState.serviceRunning) { batteryExempt = onCheckBatteryOptimizationExempt() }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .systemBarsPadding()
    ) {
        // ── Header ──────────────────────────────────────────────────────────
        StatusHeader(uiState = uiState)

        // ── Warning banners ─────────────────────────────────────────────────
        if (!uiState.permissionsGranted) {
            BannerButton(
                text = "Permessi Bluetooth mancanti",
                actionLabel = "Concedi",
                color = Danger,
            ) { onRequestPermissions(permissionLauncher) }
        }
        if (!batteryExempt) {
            BannerButton(
                text = "Ottimizzazione batteria attiva (potrebbe bloccare BLE)",
                actionLabel = "Disabilita",
                color = Color(0xFFF5A623),
            ) { onRequestDisableBatteryOptimization(batteryLauncher) }
        }

        // ── Tab bar ─────────────────────────────────────────────────────────
        TabBar(selectedTab = selectedTab, onSelect = { selectedTab = it })

        // ── Tab content ─────────────────────────────────────────────────────
        Box(modifier = Modifier.fillMaxSize()) {
            when (selectedTab) {
                Tab.SETTINGS   -> SettingsTab(
                    uiState = uiState, apiKey = apiKey, model = model,
                    onApiKeyChange = { apiKey = it }, onModelChange = { model = it },
                    onSave = { viewModel.saveSettings(apiKey = apiKey, model = model); viewModel.updatePermissions(onCheckPermissions()) },
                    onRestart = { viewModel.restartBridgeService() },
                    onRefreshModels = { viewModel.loadAvailableModels(apiKey) },
                )
                Tab.CONTAINERS -> ContainersTab()
                Tab.LOGS       -> LogsTab(logs = uiState.logs)
            }
        }
    }
}

// ─── Status header ────────────────────────────────────────────────────────────

@Composable
private fun StatusHeader(uiState: UiState) {
    val isRunning = uiState.serviceRunning
    val dotColor by animateColorAsState(
        targetValue = if (isRunning) AccentGrn else Danger,
        animationSpec = tween(600), label = "dot"
    )
    Surface(color = SurfCard, modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(10.dp)
                    .clip(CircleShape)
                    .background(dotColor)
            )
            Spacer(Modifier.width(10.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "Gemini Bridge",
                    fontSize = 17.sp, fontWeight = FontWeight.SemiBold,
                    color = TextPrim,
                )
                Text(
                    text = uiState.bridgeStatus,
                    fontSize = 12.sp, color = TextSec,
                    maxLines = 1,
                )
            }
        }
    }
}

// ─── Banner button ────────────────────────────────────────────────────────────

@Composable
private fun BannerButton(text: String, actionLabel: String, color: Color, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(color.copy(alpha = 0.12f))
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text = text, fontSize = 12.sp, color = color, modifier = Modifier.weight(1f))
        TextButton(onClick = onClick) {
            Text(actionLabel, color = color, fontSize = 12.sp)
        }
    }
}

// ─── Tab bar ─────────────────────────────────────────────────────────────────

@Composable
private fun TabBar(selectedTab: Tab, onSelect: (Tab) -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(SurfCard)
            .padding(horizontal = 12.dp, vertical = 0.dp),
    ) {
        Tab.entries.forEach { tab ->
            val selected = tab == selectedTab
            val labelColor by animateColorAsState(
                if (selected) Accent else TextSec, label = "tab_${tab.name}"
            )
            Column(
                modifier = Modifier
                    .weight(1f)
                    .clickable { onSelect(tab) }
                    .padding(vertical = 10.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Icon(tab.icon, contentDescription = tab.label, tint = labelColor, modifier = Modifier.size(20.dp))
                Spacer(Modifier.height(2.dp))
                Text(tab.label, fontSize = 11.sp, color = labelColor)
                if (selected) {
                    Spacer(Modifier.height(4.dp))
                    Box(Modifier.width(24.dp).height(2.dp).clip(RoundedCornerShape(1.dp)).background(Accent))
                }
            }
        }
    }
}

// ─── Settings tab ─────────────────────────────────────────────────────────────

@Composable
private fun SettingsTab(
    uiState: UiState,
    apiKey: String, model: String,
    onApiKeyChange: (String) -> Unit, onModelChange: (String) -> Unit,
    onSave: () -> Unit, onRestart: () -> Unit, onRefreshModels: () -> Unit,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            AppCard {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    SectionTitle("API & Modello")
                    OutlinedTextField(
                        value = apiKey, onValueChange = onApiKeyChange,
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("Gemini API Key") },
                        visualTransformation = PasswordVisualTransformation(),
                        singleLine = true,
                        colors = outlinedColors(),
                    )
                    OutlinedTextField(
                        value = model, onValueChange = onModelChange,
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("Modello") },
                        singleLine = true,
                        colors = outlinedColors(),
                    )
                }
            }
        }

        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                PrimaryButton(label = "💾 Salva", modifier = Modifier.weight(1f), onClick = onSave)
                SecondaryButton(label = "↺ Restart", modifier = Modifier.weight(1f), onClick = onRestart)
                SecondaryButton(label = "⟳ Modelli", modifier = Modifier.weight(1f), onClick = onRefreshModels)
            }
        }

        if (uiState.modelsLoading) {
            item {
                Text("Caricamento modelli…", fontSize = 12.sp, color = TextSec)
            }
        } else if (uiState.modelsError.isNotBlank()) {
            item {
                Text(uiState.modelsError, fontSize = 12.sp, color = Danger)
            }
        }

        if (uiState.availableModels.isNotEmpty()) {
            item { SectionTitle("Modelli disponibili") }
            items(uiState.availableModels) { name ->
                Surface(
                    modifier = Modifier.fillMaxWidth().clickable { onModelChange(name) },
                    color = SurfCard2, shape = RoundedCornerShape(8.dp),
                ) {
                    Text(
                        text = name, fontSize = 13.sp, color = TextPrim,
                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                    )
                }
            }
        }
    }
}

// ─── Containers tab ───────────────────────────────────────────────────────────

@Composable
private fun ContainersTab() {
    val containers = BridgeRuntimeState.containers.values.toList()
    val activeId   = BridgeRuntimeState.activeContainerId

    if (containers.isEmpty()) {
        Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("📚", fontSize = 48.sp)
                Spacer(Modifier.height(12.dp))
                Text("Nessuna libreria caricata", color = TextSec, fontSize = 14.sp)
                Spacer(Modifier.height(6.dp))
                Text("Carica un container dal Mac", color = TextSec, fontSize = 12.sp)
            }
        }
        return
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item { SectionTitle("Knowledge Base (${containers.size})") }
        items(containers) { container ->
            val isActive = container.id == activeId
            val borderColor = if (isActive) AccentGrn else Color.Transparent
            Surface(
                color = if (isActive) AccentGrn.copy(alpha = 0.10f) else SurfCard2,
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .border(1.dp, borderColor, RoundedCornerShape(12.dp))
                    .clickable {
                        BridgeRuntimeState.activeContainerId =
                            if (isActive) null else container.id
                    },
            ) {
                Row(
                    modifier = Modifier.padding(14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(container.name, fontWeight = FontWeight.SemiBold, color = TextPrim, fontSize = 15.sp)
                        Text("${container.chunks.size} chunk", color = TextSec, fontSize = 12.sp)
                    }
                    if (isActive) {
                        Icon(Icons.Default.Check, contentDescription = "Attivo", tint = AccentGrn, modifier = Modifier.size(20.dp))
                    }
                }
            }
        }
    }
}

// ─── Logs tab ─────────────────────────────────────────────────────────────────

@Composable
private fun LogsTab(logs: List<String>) {
    val listState = rememberLazyListState()
    LaunchedEffect(logs.size) {
        if (logs.isNotEmpty()) listState.animateScrollToItem(logs.size - 1)
    }
    LazyColumn(
        state = listState,
        modifier = Modifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        items(logs) { line ->
            Text(
                text = line.substringAfter("| "),
                fontSize = 11.sp,
                color = when {
                    line.contains("error", ignoreCase = true) -> Danger
                    line.contains("success", ignoreCase = true) || line.contains("saved") -> AccentGrn
                    else -> TextSec
                },
                fontFamily = FontFamily.Monospace,
                lineHeight = 16.sp,
            )
        }
    }
}

// ─── Reusable components ──────────────────────────────────────────────────────

@Composable
private fun AppCard(content: @Composable ColumnScope.() -> Unit) {
    Surface(color = SurfCard2, shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp), content = content)
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text = text, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
        color = TextSec, letterSpacing = 0.8.sp,
        modifier = Modifier.padding(bottom = 2.dp))
}

@Composable
private fun PrimaryButton(label: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Button(
        onClick = onClick, modifier = modifier,
        shape = RoundedCornerShape(10.dp),
        colors = ButtonDefaults.buttonColors(containerColor = Accent),
    ) { Text(label, fontSize = 12.sp) }
}

@Composable
private fun SecondaryButton(label: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    OutlinedButton(
        onClick = onClick, modifier = modifier,
        shape = RoundedCornerShape(10.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, TextSec.copy(alpha = 0.4f)),
    ) { Text(label, fontSize = 12.sp, color = TextSec) }
}

@Composable
private fun outlinedColors() = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = Accent,
    unfocusedBorderColor = TextSec.copy(alpha = 0.3f),
    focusedLabelColor = Accent,
    unfocusedLabelColor = TextSec,
    focusedTextColor = TextPrim,
    unfocusedTextColor = TextPrim,
    cursorColor = Accent,
)
