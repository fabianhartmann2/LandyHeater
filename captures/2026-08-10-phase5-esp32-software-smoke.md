# Phase-5-Software-Smoke auf dem DFR0654 – 2026-08-10

## Aufbau und Umfang

- DFRobot FireBeetle 2 ESP32-E V1.0, SKU DFR0654
- MicroPython `ESP32_GENERIC` v1.28.0 vom 6. April 2026
- Board ausschließlich über USB im freigegebenen Software-Smoke-Aufbau
- keine UART-, Pin-, I2C-, 1-Wire-, Controller- oder Protokolloperation
- künstliche UTC-/Timerdaten; keine echte RTC und keine Heizungssteuerung

Übertragen wurden ausschließlich `boot.py`, `main.py` sowie die benötigten
hardwarefreien Module aus `app`, `services` und `tools`. Bereits vorhandene
Diagnosedateien blieben unbenutzt.

## Identität

```text
(name='micropython', version=(1, 28, 0, ''), _machine='Generic ESP32 module with ESP32', _mpy=11014, _build='ESP32_GENERIC', _thread='GIL')
(sysname='esp32', nodename='esp32', release='1.28.0', version='v1.28.0 on 2026-04-06', machine='Generic ESP32 module with ESP32')
```

## Ergebnis

```text
PHASE 5 SOFTWARE-ONLY SMOKE PASS: 8/8 iterations; no hardware opened
MicroPython heap free: before=152896 after_import=127456 after_warmup=128608 after=128608 bytes
PHASE5_USB_SMOKE_PASS_V1
```

Der Core-Import benötigte gegenüber der Runner-Baseline 25.440 Bytes. Nach
dem Aufwärmlauf standen 128.608 Bytes frei; nach allen acht gemessenen
Lebenszyklen und der Speicherbereinigung waren es erneut exakt 128.608 Bytes.
Damit gab es im Messblock keinen verbleibenden Heapverlust.

Geprüft wurden pro Lebenszyklus:

- feste RTC-Vertrauensprobe nur in-memory
- Boot-/Konfigurations-Fence des Schedulers
- genau ein Timer-Intent an der natürlichen Minutenkante
- zweistufige Autorisierung und synthetische Requested-State-Bestätigung
- kein zweites Intent in derselben Minute
- manueller Override und dessen Once-only-Latch
- separater, absichtlich abgelaufener Auftrag ohne Retry
- echte MicroPython-`ticks_ms`-/`ticks_add`-/`ticks_diff`-Primitiven
- mindestens 32 KiB freier Heap und begrenzter Drift nach dem Aufwärmen

Nach einem Reset blieb der reguläre Einstiegspunkt passiv:

```text
Landy Heater safe boot; UART inactive; protocol TX disabled
```

## Aussagegrenze

Dieser Nachweis bestätigt die MicroPython-Lauffähigkeit des hardwarefreien
TimeService-/Scheduler-Kerns und seine Heap-Stabilität auf dem realen ESP32.
Er bestätigt ausdrücklich noch keine DS3231-, DS18B20-, UART-, Pegelwandler-
oder Heizungsintegration. Diese elektrischen Prüfungen bleiben Phase 13.
