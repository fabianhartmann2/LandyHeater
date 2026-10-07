# DFR0975-U bounded heater active cycle — 2026-10-07

## Scope

This Phase-13 gate flashed the reviewed heater activation application and ran
one supervised Autoterm cycle through the production protocol/controller
path. The heater used fixed power level 1 for seven minutes from START, giving
at least five minutes for startup and settling plus about two minutes of
stable operation. The gate then supervised controlled shutdown until a valid
STATUS response confirmed synchronized `OFF`.

The direct, permanently connected level shifter and its accepted residual
reset/software risk are documented separately. D12/GPIO12 remained unused.
The physical 12-V cutoff stayed accessible throughout the run.

## Authorized image and flash result

The owner authorized exactly:

```text
USB-only bestätigt. App-Flash ohne Erase bei 0x10000 freigegeben: f02d59e7d8c501c387e837cbbbc38ec9725387e8ac9db18c1395df084298ca3d
```

Only the 2,109,520-byte application was written at `0x10000`. No full erase,
bootloader, partition-table or VFS write occurred. Esptool verified the write.
An independent full readback of exactly 2,109,520 bytes from `0x10000` was
byte-identical to the retained image and had SHA-256:

```text
f02d59e7d8c501c387e837cbbbc38ec9725387e8ac9db18c1395df084298ca3d
```

After manual reset, the passive gate confirmed MicroPython 1.28.0, the
DFR0975-U N16R8 board identity, frozen UART2 GPIO14/GPIO13 activation, the
selected approved direct interface, the absent gate still unapproved and both
radio interfaces inactive. It sent no UART frame.

## Two-stage active authorization

With heater 12 V off, the owner confirmed:

```text
DFR0975U_USB_ONLY_DIRECT_TX_ACTIVE_CYCLE_ARM_HEATER_12V_OFF_V1
```

Arming opened the configured runtime and reported zero writes. The first
attempt to trigger the cycle was rejected before synchronization or any START
because reconnecting the USB command session had discarded the RAM-only armed
state. Heater 12 V was switched off again. The probe was re-armed with zero
writes inside one persistent USB session, which remained open for the cycle.

After 12 V was enabled, the owner confirmed:

```text
DFR0975U_DIRECT_TX_HEATER_POWER1_SEVEN_MINUTES_AUTO_SHUTDOWN_V1
```

The probe exposed no arbitrary-send or external-temperature function. It
fixed the command to power level 1 and seven minutes, capped START and
SHUTDOWN at two attempts each, required radios to remain inactive and compared
all production configuration/storage file signatures before and after.

## Result

The runtime synchronized from `OFF`, observed `STARTING`, reached `RUNNING`,
kept the requested seven-minute interval, observed `SHUTTING_DOWN` and
returned to synchronized `OFF`. The final target output was:

```text
phase=ready
heater_state=off
start_requests=1
shutdown_requests=1
storage_unchanged=True
DFR0975U_ACTIVE_CYCLE_RUNNING_TO_OFF_PASS_V1
```

The returned measurement record was:

```text
runtime_minutes=7
power_level=1
status_requests=576
voltage=12.2
```

Exactly one START and one SHUTDOWN request were needed. Production storage
remained unchanged, both radios stayed inactive, the runtime closed its UART
normally after confirmed `OFF`, the persistent USB session was closed and the
owner then confirmed heater 12 V off.

## Conclusion and remaining boundary

The real DFR0975-U product path, existing level shifter, Autoterm protocol,
controller state machine and controlled shutdown are proven together for one
bounded power-level-1 cycle. This closes the planned Phase-13 active heater
cycle gate. It does not authorize higher power levels, temperature mode,
external-temperature transmission, repeated endurance cycles or automatic
startup at boot. Phase 13 still retains the live per-ROM setup-assistant
sensor assignment and a replacement-battery RTC gate; Phase 12 hardening also
remains separate.
