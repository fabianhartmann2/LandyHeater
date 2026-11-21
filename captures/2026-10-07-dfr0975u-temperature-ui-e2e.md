# DFR0975-U temperature-mode Web-UI target evidence — 2026-10-07

## Installed image

The 2,132,464-byte application with SHA-256
`84eb394f7be14a853283d52db6d80c6d2f6ea14bc6e22704864acdb5d5a45881`
was explicitly authorized, written app-only at `0x10000` without a full erase
and independently read back byte-identical. After reset, the phone joined the
protected AP, the UI reached connected state and all three assigned DS18B20
temperatures were visible.

## Roof-tent temperature run

- Heater 12 V was enabled only after the UI showed synchronized `OFF`.
- Roof-tent control used a target at least 5 °C above its live reading.
- The heater reported `TEMP_MONITORING`, heated for at least seven minutes,
  retained a connected UI and continuously updated the selected temperature.
- The controller electronics and accepted level shifter remained cool.
- UI Stop completed the controlled shutdown and reached confirmed `OFF`; 12 V
  remained present until the physical cooldown had ended.

This accepts the roof-tent sensor selection, periodic external-temperature
path and bounded `TEMP_MONITORING` shutdown against the real heater.

## Cabin temperature run and UI defect

The cabin sensor was selected, but the user pressed **Abbrechen** rather than
**Heizung starten**. The dialog nevertheless submitted the form and started
the heater. The resulting cabin session itself behaved correctly: it reported
`TEMP_MONITORING`, heated for at least seven minutes, updated the cabin
temperature, retained the UI connection, kept the electronics cool and
returned through controlled Stop to confirmed `OFF` after cooldown.

The cabin sensor/report/runtime path is therefore real-heater confirmed, but
the cancel action is rejected as a safety-relevant UI defect. Inspection found
that both cancel buttons had implicit submit behavior and the JavaScript submit
handler did not distinguish a dialog cancel from an intentional start. The
same latent pattern existed in the timer editor.

## Corrected candidate and remaining target gate

The correction makes all four Start/Timer close and cancel controls explicit
non-submit buttons with direct close handlers. Both submit handlers also reject
a legacy cancel submit as defense in depth. Targeted source/UI tests pass, and
two clean canonical builds produced 15/15 byte-identical outputs.

```text
offset: 0x10000
size:   2132848 bytes
sha256: 4bf2176c96842e2438c339c6d8f2f2c6e3a3c452b5878eabdf47fe2a74df5314
erase:  no full-chip erase
```

This corrected candidate has not been flashed. Its required target check is
deliberately non-heating: with heater 12 V off, both Start-dialog cancel paths
must leave the heater request OFF, and both Timer-dialog cancel paths must
leave the timer collection unchanged. The already completed seven-minute
heater runs do not need to be repeated because the correction changes only the
frozen browser assets.
