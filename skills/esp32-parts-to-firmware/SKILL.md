---
name: "esp32-parts-to-firmware"
description: "Use when given a parts list or hardware idea for an ESP32/Arduino/Pi build to produce wiring, pin map, firmware and a bench test plan."
---

# Parts list to firmware

Based on a saved workflow: give the agent the exact parts list and let it produce wiring and firmware together, so pins and code always agree.

## 1. Pin down the hardware
- Collect the exact board variant (e.g. XIAO ESP32-S3, ESP32-C5, DevKitC), every module part number, power source and voltage.
- If a part number is missing or ambiguous, ask once and list your assumptions.
- Note each part's logic level (3.3 V vs 5 V), interface (I2C, SPI, UART, GPIO, PWM, ADC) and current draw.

## 2. Pin map first
- Produce a table: signal, board pin (GPIO number and silkscreen label), module pin, notes.
- Avoid strapping/boot pins, flash/PSRAM pins and input-only pins for outputs on the chosen variant. Check the variant's pinout rather than assuming the generic ESP32 one.
- Flag level-shifting, pull-ups, decoupling caps and current limits (LED resistors, servo or motor supply separate from the 3.3 V rail).
- Share I2C buses deliberately and list addresses to catch conflicts.

## 3. Wiring
- Give a wiring list ordered by connection (power and ground first), plus a simple ASCII or Mermaid diagram.
- Call out anything that can damage parts if wired wrong.

## 4. Firmware
- For a Raspberry Pi, write Python with `gpiozero`. For a microcontroller, default to Arduino framework on PlatformIO unless the user prefers ESP-IDF, MicroPython or Arduino IDE. Include `platformio.ini` with board, framework, monitor speed and pinned library versions.
- Pin numbers live in one config header that matches the pin map table exactly.
- Use only library APIs that exist in the pinned versions; check headers or examples instead of guessing.
- Bring up one peripheral at a time, with serial logging for each.

## 5. Bench test plan
- Steps in order: power-only check with multimeter, flash a blink/serial test, then each peripheral alone, then the full program.
- For each step: what to observe and the likely cause if it fails.

## 6. Enclosure hook (optional)
- If the user 3D prints, list board and module dimensions, mounting-hole positions, connector and antenna clearances, and ventilation needs, so a case can be modeled.

## Wireless projects
- For Wi-Fi/BLE scanning or logging builds (e.g. wardriving loggers), keep them passive: record broadcast metadata only, never connect to or interfere with networks you don't own, and note local laws.