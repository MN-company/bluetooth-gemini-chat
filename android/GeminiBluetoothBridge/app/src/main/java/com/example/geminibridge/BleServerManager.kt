package com.example.geminibridge

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattServer
import android.bluetooth.BluetoothGattServerCallback
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.bluetooth.le.BluetoothLeAdvertiser
import android.content.Context
import android.os.Build
import android.os.ParcelUuid
import java.util.concurrent.ConcurrentHashMap
import java.util.Locale
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.yield

@SuppressLint("MissingPermission")
class BleServerManager(
    context: Context,
    private val scope: CoroutineScope,
    private val bridgeId: String,
    private val onPromptJson: suspend (String, String) -> Unit,
    private val onLog: (String) -> Unit,
    private val onBridgeStatus: (String) -> Unit,
) {
    private val appContext = context.applicationContext
    private val bluetoothManager =
        appContext.getSystemService(BluetoothManager::class.java)
    private val bluetoothAdapter: BluetoothAdapter? = bluetoothManager?.adapter

    private val frameAssemblersByDevice = ConcurrentHashMap<String, BleFrameAssembler>()
    private val transportIds = TransportIdGenerator()

    private val mtuByDevice = ConcurrentHashMap<String, Int>()
    private val connectedDevices = ConcurrentHashMap<String, BluetoothDevice>()
    private val sendMutexByDevice = ConcurrentHashMap<String, Mutex>()

    private var gattServer: BluetoothGattServer? = null
    private var notifyCharacteristic: BluetoothGattCharacteristic? = null
    private var advertiseCallback: AdvertiseCallback? = null
    private val normalizedBridgeId = normalizeBridgeId(bridgeId)

    @Volatile
    private var lastActiveDeviceAddress: String? = null

    @Volatile
    private var advertisingActive: Boolean = false

    private val gattCallback = object : BluetoothGattServerCallback() {
        override fun onConnectionStateChange(device: BluetoothDevice, status: Int, newState: Int) {
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    connectedDevices[device.address] = device
                    sendMutexByDevice.putIfAbsent(device.address, Mutex())
                    lastActiveDeviceAddress = device.address
                    onLog("BLE connected: ${device.address} (bridgeId=$normalizedBridgeId)")
                    requestLowLatencyPhy(device)
                    onBridgeStatus("Connected clients: ${connectedDevices.size}")
                }

                BluetoothProfile.STATE_DISCONNECTED -> {
                    connectedDevices.remove(device.address)
                    if (lastActiveDeviceAddress == device.address) {
                        lastActiveDeviceAddress = connectedDevices.keys.firstOrNull()
                    }
                    mtuByDevice.remove(device.address)
                    frameAssemblersByDevice.remove(device.address)
                    sendMutexByDevice.remove(device.address)
                    onLog("BLE disconnected: ${device.address} (bridgeId=$normalizedBridgeId)")
                    if (connectedDevices.isNotEmpty()) {
                        onBridgeStatus("Connected clients: ${connectedDevices.size}")
                    } else if (advertisingActive) {
                        onBridgeStatus("Advertising BLE bridge #$normalizedBridgeId")
                    } else {
                        onBridgeStatus("BLE idle, waiting for advertising restart")
                    }
                }
            }

            if (status != BluetoothGatt.GATT_SUCCESS) {
                onLog("Connection status warning: $status")
            }
        }

        override fun onMtuChanged(device: BluetoothDevice, mtu: Int) {
            mtuByDevice[device.address] = mtu
            onLog("MTU changed (${device.address}): $mtu")
        }

        override fun onPhyUpdate(device: BluetoothDevice, txPhy: Int, rxPhy: Int, status: Int) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val statusText = if (status == BluetoothGatt.GATT_SUCCESS) "ok" else "status=$status"
            onLog("PHY update (${device.address}): tx=${phyToText(txPhy)} rx=${phyToText(rxPhy)} ($statusText)")
        }

        override fun onPhyRead(device: BluetoothDevice, txPhy: Int, rxPhy: Int, status: Int) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val statusText = if (status == BluetoothGatt.GATT_SUCCESS) "ok" else "status=$status"
            onLog("PHY read (${device.address}): tx=${phyToText(txPhy)} rx=${phyToText(rxPhy)} ($statusText)")
        }

        override fun onDescriptorWriteRequest(
            device: BluetoothDevice,
            requestId: Int,
            descriptor: BluetoothGattDescriptor,
            preparedWrite: Boolean,
            responseNeeded: Boolean,
            offset: Int,
            value: ByteArray,
        ) {
            if (descriptor.uuid == BleConstants.cccdUuid) {
                descriptor.value = value
            }

            if (responseNeeded) {
                gattServer?.sendResponse(device, requestId, BluetoothGatt.GATT_SUCCESS, 0, null)
            }
        }

        override fun onCharacteristicWriteRequest(
            device: BluetoothDevice,
            requestId: Int,
            characteristic: BluetoothGattCharacteristic,
            preparedWrite: Boolean,
            responseNeeded: Boolean,
            offset: Int,
            value: ByteArray,
        ) {
            try {
                if (characteristic.uuid == BleConstants.writeCharUuid) {
                    val frame = BleFrameCodec.decodeFrame(value)
                    if (frame == null) {
                        onLog("Received invalid BLE frame")
                    } else {
                        val assembler = frameAssemblersByDevice.getOrPut(device.address) { BleFrameAssembler() }
                        val payload = assembler.addFrame(frame)
                        if (payload != null) {
                            lastActiveDeviceAddress = device.address
                            val json = if (
                                payload.size >= 3 &&
                                payload[0] == 'g'.code.toByte() &&
                                payload[1] == 'z'.code.toByte() &&
                                payload[2] == 1.toByte()
                            ) {
                                onLog("Decompressing payload (${payload.size} bytes)")
                                try {
                                    java.util.zip.GZIPInputStream(java.io.ByteArrayInputStream(payload, 3, payload.size - 3)).bufferedReader(Charsets.UTF_8).use { it.readText() }
                                } catch (e: Exception) {
                                    onLog("Decompression failed: ${e.message}")
                                    ""
                                }
                            } else {
                                payload.toString(Charsets.UTF_8)
                            }

                            if (json.isNotEmpty()) {
                                onLog("Received request JSON (${json.length} chars) from ${device.address}")
                                scope.launch {
                                    onPromptJson(json, device.address)
                                }
                            }
                        }
                    }
                }

                if (responseNeeded) {
                    gattServer?.sendResponse(device, requestId, BluetoothGatt.GATT_SUCCESS, 0, null)
                }
            } catch (t: Throwable) {
                onLog("Write handling failed: ${t.message}")
                if (responseNeeded) {
                    gattServer?.sendResponse(
                        device,
                        requestId,
                        BluetoothGatt.GATT_FAILURE,
                        0,
                        null,
                    )
                }
            }
        }
    }

    fun start(): Result<Unit> {
        val adapter = bluetoothAdapter ?: return Result.failure(
            IllegalStateException("Bluetooth adapter not available")
        )

        if (!adapter.isEnabled) {
            return Result.failure(IllegalStateException("Bluetooth is disabled"))
        }

        val leAdvertiser = adapter.bluetoothLeAdvertiser ?: return Result.failure(
            IllegalStateException("BLE advertiser not available on this phone")
        )

        val server = bluetoothManager?.openGattServer(appContext, gattCallback)
            ?: return Result.failure(IllegalStateException("Failed to open GATT server"))

        gattServer = server
        val service = createService()
        if (!server.addService(service)) {
            server.close()
            gattServer = null
            return Result.failure(IllegalStateException("Failed to add GATT service"))
        }

        return startAdvertisingInternal(leAdvertiser)
    }

    fun stop() {
        val adapter = bluetoothAdapter
        val callback = advertiseCallback
        if (adapter != null && callback != null) {
            adapter.bluetoothLeAdvertiser?.stopAdvertising(callback)
        }
        advertiseCallback = null
        advertisingActive = false

        gattServer?.close()
        gattServer = null
        notifyCharacteristic = null
        lastActiveDeviceAddress = null
        connectedDevices.clear()
        mtuByDevice.clear()
        frameAssemblersByDevice.clear()
        sendMutexByDevice.clear()
        onBridgeStatus("BLE bridge stopped")
    }

    fun isOperational(): Boolean {
        return gattServer != null && (advertisingActive || connectedDevices.isNotEmpty())
    }

    fun ensureAdvertising(): Result<Unit> {
        if (connectedDevices.isNotEmpty()) return Result.success(Unit)
        if (advertisingActive) return Result.success(Unit)

        val adapter = bluetoothAdapter ?: return Result.failure(
            IllegalStateException("Bluetooth adapter not available")
        )
        if (!adapter.isEnabled) {
            return Result.failure(IllegalStateException("Bluetooth disabled"))
        }

        val advertiser = adapter.bluetoothLeAdvertiser ?: return Result.failure(
            IllegalStateException("BLE advertiser unavailable")
        )
        if (gattServer == null) {
            return Result.failure(IllegalStateException("GATT server is not started"))
        }

        advertiseCallback?.let { callback ->
            runCatching { advertiser.stopAdvertising(callback) }
            advertiseCallback = null
        }

        return startAdvertisingInternal(advertiser)
    }

    suspend fun sendJson(
        jsonMessage: String,
        targetAddress: String? = null,
        highPriority: Boolean = false,
    ) {
        val device = if (!targetAddress.isNullOrBlank()) {
            connectedDevices[targetAddress]
        } else {
            val active = lastActiveDeviceAddress?.let { connectedDevices[it] }
            active ?: connectedDevices.values.firstOrNull()
        } ?: throw IllegalStateException("No connected BLE central device")

        val sendLock = sendMutexByDevice.getOrPut(device.address) { Mutex() }
        sendLock.withLock {
            val mtu = mtuByDevice[device.address] ?: BleConstants.defaultAttMtu
            val mtuPayloadMax = maxOf(BleConstants.defaultMaxPacketSize, mtu - 3)
            val maxPacketSize = minOf(BleConstants.maxGattAttributeValueBytes, mtuPayloadMax)
            val transportId = transportIds.next()

            val packets = BleFrameCodec.encodeMessage(
                transportId = transportId,
                payload = jsonMessage.toByteArray(Charsets.UTF_8),
                maxPacketSize = maxPacketSize,
            )

            val multiClient = connectedDevices.size > 1
            val throttleEvery = when {
                highPriority || packets.size <= 12 -> 0
                multiClient && packets.size > 140 -> 6
                multiClient -> 4
                packets.size > 180 -> 14
                packets.size > 90 -> 10
                else -> 6
            }
            val throttleDelayMs = when {
                highPriority || throttleEvery == 0 -> 0L
                multiClient -> 1L
                else -> 1L
            }

            packets.forEachIndexed { idx, packet ->
                val notified = notifyPacket(device, packet)
                if (!notified) {
                    throw IllegalStateException("Failed to notify BLE packet")
                }
                if (throttleEvery > 0 && (idx + 1) % throttleEvery == 0) {
                    if (throttleDelayMs > 0L) {
                        delay(throttleDelayMs)
                    } else {
                        yield()
                    }
                }
            }
        }
    }

    private fun startAdvertisingInternal(advertiser: BluetoothLeAdvertiser): Result<Unit> {
        val callback = object : AdvertiseCallback() {
            override fun onStartSuccess(settingsInEffect: AdvertiseSettings) {
                advertisingActive = true
                onLog("BLE advertising started (bridgeId=$normalizedBridgeId)")
                onBridgeStatus("Advertising BLE bridge #$normalizedBridgeId")
            }

            override fun onStartFailure(errorCode: Int) {
                val reason = advertiseErrorToText(errorCode)
                advertisingActive = errorCode == AdvertiseCallback.ADVERTISE_FAILED_ALREADY_STARTED
                onLog("BLE advertising failed: $reason")
                if (advertisingActive) {
                    onBridgeStatus("Advertising BLE bridge #$normalizedBridgeId")
                } else {
                    onBridgeStatus("BLE advertising failed: $reason")
                }
            }
        }

        val settings = AdvertiseSettings.Builder()
            .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY)
            .setConnectable(true)
            .setTimeout(0)
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_HIGH)
            .build()

        val data = AdvertiseData.Builder()
            .addServiceUuid(ParcelUuid(BleConstants.serviceUuid))
            .setIncludeDeviceName(false)
            .build()
        val scanResponse = AdvertiseData.Builder()
            .addManufacturerData(BleConstants.bridgeManufacturerId, bridgeIdBytes(normalizedBridgeId))
            .setIncludeDeviceName(false)
            .build()

        return runCatching {
            advertiseCallback = callback
            onBridgeStatus("Starting BLE advertising...")
            advertiser.startAdvertising(settings, data, scanResponse, callback)
        }
    }

    private fun createService(): BluetoothGattService {
        val service = BluetoothGattService(
            BleConstants.serviceUuid,
            BluetoothGattService.SERVICE_TYPE_PRIMARY,
        )

        val writeCharacteristic = BluetoothGattCharacteristic(
            BleConstants.writeCharUuid,
            BluetoothGattCharacteristic.PROPERTY_WRITE or BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE,
            BluetoothGattCharacteristic.PERMISSION_WRITE,
        )

        val notifyCharacteristic = BluetoothGattCharacteristic(
            BleConstants.notifyCharUuid,
            BluetoothGattCharacteristic.PROPERTY_NOTIFY,
            BluetoothGattCharacteristic.PERMISSION_READ,
        )

        val cccd = BluetoothGattDescriptor(
            BleConstants.cccdUuid,
            BluetoothGattDescriptor.PERMISSION_READ or BluetoothGattDescriptor.PERMISSION_WRITE,
        )
        notifyCharacteristic.addDescriptor(cccd)

        service.addCharacteristic(writeCharacteristic)
        service.addCharacteristic(notifyCharacteristic)
        this.notifyCharacteristic = notifyCharacteristic

        return service
    }

    private fun notifyPacket(device: BluetoothDevice, packet: ByteArray): Boolean {
        val server = gattServer ?: return false
        val characteristic = notifyCharacteristic ?: return false

        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            server.notifyCharacteristicChanged(device, characteristic, false, packet) ==
                BluetoothStatusCodes.SUCCESS
        } else {
            @Suppress("DEPRECATION")
            characteristic.value = packet
            @Suppress("DEPRECATION")
            server.notifyCharacteristicChanged(device, characteristic, false)
        }
    }

    private fun requestLowLatencyPhy(device: BluetoothDevice) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val server = gattServer ?: return
        runCatching {
            server.setPreferredPhy(
                device,
                BluetoothDevice.PHY_LE_2M_MASK,
                BluetoothDevice.PHY_LE_2M_MASK,
                BluetoothDevice.PHY_OPTION_NO_PREFERRED,
            )
            server.readPhy(device)
        }.onFailure {
            onLog("setPreferredPhy failed (${device.address}): ${it.message}")
        }
    }

    private fun phyToText(phy: Int): String {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return phy.toString()
        return when (phy) {
            BluetoothDevice.PHY_LE_1M -> "1M"
            BluetoothDevice.PHY_LE_2M -> "2M"
            BluetoothDevice.PHY_LE_CODED -> "CODED"
            else -> phy.toString()
        }
    }

    private fun advertiseErrorToText(errorCode: Int): String {
        return when (errorCode) {
            AdvertiseCallback.ADVERTISE_FAILED_DATA_TOO_LARGE -> "DATA_TOO_LARGE (1)"
            AdvertiseCallback.ADVERTISE_FAILED_TOO_MANY_ADVERTISERS -> "TOO_MANY_ADVERTISERS (2)"
            AdvertiseCallback.ADVERTISE_FAILED_ALREADY_STARTED -> "ALREADY_STARTED (3)"
            AdvertiseCallback.ADVERTISE_FAILED_INTERNAL_ERROR -> "INTERNAL_ERROR (4)"
            AdvertiseCallback.ADVERTISE_FAILED_FEATURE_UNSUPPORTED -> "FEATURE_UNSUPPORTED (5)"
            else -> "UNKNOWN ($errorCode)"
        }
    }

    private fun normalizeBridgeId(raw: String): String {
        val cleaned = raw.trim().uppercase(Locale.US).replace("[^0-9A-F]".toRegex(), "")
        if (cleaned.length >= 12) {
            return cleaned.take(12)
        }
        return cleaned.padEnd(12, '0')
    }

    private fun bridgeIdBytes(raw: String): ByteArray {
        return runCatching {
            val clean = normalizeBridgeId(raw)
            ByteArray(clean.length / 2) { idx ->
                clean.substring(idx * 2, idx * 2 + 2).toInt(16).toByte()
            }
        }.getOrDefault(byteArrayOf(0, 0, 0, 0, 0, 0))
    }
}
