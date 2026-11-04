# Ode — Give Muse a body: hardware

Muse's hands are an ESP32-C3 Bluetooth keyboard and mouse. Its eyes come from AirPlay. The board connects outward over Wi-Fi/TCP to the Mac server; USB is for board power, backup and flashing only.

## Parts and wiring

- An owner-controlled iPad with AirPlay mirroring and Bluetooth HID support.
- An ESP32-C3 development board, approximately $5 depending on supplier.
- A USB data cable and Mac on the same trusted local network as the board/iPad.

The ~$5 estimate covers only the development board; the Mac, iPad, USB cable, network and cloud API usage are additional requirements. There is **no GPIO wiring to the iPad**. Connect only the ESP32 to the Mac's USB port. Pair the firmware's Bluetooth device with the iPad. Do not select an unrelated board or flash a phone/iPad.

## Install the build tools

Install `arduino-cli`, Python, and the ESP32 core; then install the BLE library. The firmware uses NimBLE's 2.x API. These commands install tools but do not select or write a board:

```sh
arduino-cli core update-index --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core install esp32:esp32@3.3.8 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli lib install 'NimBLE-Arduino@2.3.9'
python -m pip install esptool
arduino-cli board list
```

The versions above are a reproducible build target; see [TESTING.md](TESTING.md) for whether this snapshot was actually compiled locally. Check board variant, flash capacity and USB settings against your hardware. Do not substitute a classic ESP32 or ESP32-S3 target for the C3.

## Back up the explicitly selected board

Set `ESP32_SERIAL_PORT` yourself after identifying the board. No supplied command auto-selects a serial port. Do not copy a port name from another device.

```sh
export ESP32_SERIAL_PORT='/dev/cu.REPLACE_WITH_YOUR_BOARD'
: "${ESP32_SERIAL_PORT:?Choose the board serial port explicitly}"
mkdir -p backups
python -m esptool --chip esp32c3 --port "$ESP32_SERIAL_PORT" flash-id
# Replace 0x400000 only after confirming the actual board's flash capacity.
python -m esptool --chip esp32c3 --port "$ESP32_SERIAL_PORT" read-flash 0 0x400000 backups/ipad-board-original.bin
python -m esptool --chip esp32c3 --port "$ESP32_SERIAL_PORT" read-flash 0 0x400000 backups/ipad-board-verify.bin
cmp backups/ipad-board-original.bin backups/ipad-board-verify.bin
shasum -a 256 backups/ipad-board-original.bin
```

Stop if either read or comparison fails. Keep backups private and off the publication tree. Board backups contain Wi-Fi credentials and device identities.

## Configure and build

```sh
cp devices/ipad/firmware/hid_bridge_ble_c3/secrets.example.h devices/ipad/firmware/hid_bridge_ble_c3/secrets.h
```

Edit the ignored `secrets.h` locally. Fill `WIFI_SSID`, `WIFI_PASS`, and `SERVER_HOST` (the Mac's LAN address). Match `SERVER_PORT` to the server's `DEVICE_PORT` (7300 by default). `BLE_NAME` defaults to **Ode iPad HID** in the template. Never put real network details in public docs or issues.

```sh
arduino-cli compile --fqbn 'esp32:esp32:esp32c3:CDCOnBoot=cdc' devices/ipad/firmware/hid_bridge_ble_c3
```

Only after board identification, verified backup and a successful build, upload to that same explicit port:

```sh
arduino-cli upload --port "$ESP32_SERIAL_PORT" --fqbn 'esp32:esp32:esp32c3:CDCOnBoot=cdc' devices/ipad/firmware/hid_bridge_ble_c3
```

## Pair and calibrate

1. Start Ode on the Mac. The firmware connects outward to the configured TCP port.
2. On the iPad, pair **Ode iPad HID** in Bluetooth settings. Enable pointer/AssistiveTouch support if required by your iPadOS setup.
3. Enable full-screen apps, choose landscape, and mirror to **Ode iPad**. Avoid floating windows and picture-in-picture during calibration.
4. In the local control UI, confirm board connection and live screen. Select the iPad profile and calibrate. Test a harmless pointer movement and Settings navigation while watching the real screen.
5. Keep board identity, Bluetooth pairing, runtime directory, calibration and ports separate from every other device. Recalibrate after geometry, pointer speed or receiver changes.

The TCP board connection has no application-layer authentication or encryption. Restrict it with your local network/firewall; never expose it through the tunnel or port forwarding. A ~$5 board is not a security boundary.
