# DFR0975-U product-autostart gate — 2026-10-07

## Scope

This Phase-13 gate installed and accepted the integrated product-autostart
application on the DFR0975-U N16R8. Heater 12 V remained off throughout. The
gate covered the exact application flash and readback, fail-closed handling of
an unprovisioned production AP, one owner-authorized local provisioning
mutation and automatic AP/captive-portal/Setup-Assistant startup after reset.

## Authorized image and flash result

The owner authorized the exact application digest
`66c0799515b45334b2f852d66a4c347614a65b2109883c9f89f253df00a68a5f` for an
app-only write at `0x10000` without a full erase. Only the 2,121,600-byte
application was written. The bootloader, partition table and VFS were
preserved. Esptool verified the write, and an independent complete readback of
the same range was byte-identical with the authorized SHA-256.

## Fail-closed first boot

The first normal reset reached the frozen entry point but stopped with
`LANDY_HEATER_PRODUCT_FAULT_V1 ConfigurationStateError`. A USB-only diagnostic
showed valid production stores, `setup_complete=false`, no stored AP password
and no home-network profiles. This is the required fail-closed behavior: the
product did not create an open or universally credentialed recovery AP.

The owner then explicitly authorized a one-time production provision over USB:
set an owner-supplied WPA2 AP password, leave Setup incomplete and keep the
home-network profile list empty. The credential value is deliberately not
recorded in Git and is not embedded in the firmware. Fresh readback confirmed
configuration generation 3, unchanged Scheduler-ledger generation 2,
`setup_complete=false`, network startup allowed, timer startup blocked, no
fault and zero home-network profiles.

## Reset and phone acceptance

After one normal reset, passive USB output contained:

```text
LANDY_HEATER_PRODUCT_AUTOSTART_V1
```

No subsequent startup fault appeared. The owner then confirmed, in order:

1. the `Landy Heater` WPA2 network was visible;
2. the phone joined it with the owner-provisioned credential;
3. the captive portal opened automatically; and
4. the incomplete Setup Assistant became fully visible after its initial
   loading interval.

No Setup values were committed during this acceptance, so Setup remains
incomplete and no home-network profile was added. Timer execution remains
blocked until Setup is completed and, while the replacement RTC is absent, a
fresh browser-time sample is supplied after reset.

## Conclusion and remaining boundary

The exact product-autostart image, normal-reset entry point, protected recovery
AP, phone association, captive-portal trigger and Setup-Assistant delivery are
accepted together on the real DFR0975-U. This closes the initial USB-only,
heater-12-V-off product-autostart gate. It does not constitute a powered-heater
autostart, timer-start, home-network/mDNS, live Setup sensor-assignment or RTC
acceptance.
