# Changelog

## 0.2.0 - Unreleased

- The IR receiver follows HAIR's windowed-receiver contract, so HAIR, or
  any integration that reads the contract, can open a capture window
  itself. Two attributes join `capturing` and `window_ends_at`:
  `capture_switch`, the entity ID of the remote's IR capture switch, and
  `capture_mode`, the capture mode the next window opens in. The switch
  is looked up by its unique ID each time, so renaming it is picked up at
  once, and it is left out while the switch is disabled. A capture mode
  change now reaches the receiver straight away.
- Entity unique IDs, names and settings are unchanged, so a 0.1.0 install
  upgrades in place.
- The README opens with installation: a button that opens BroadSniff in
  HACS, the steps to add it as a custom repository or by hand, and a
  button that starts the setup.
- The README explains what RF capture can and cannot do today.
- The validation workflow can be started by hand.

## 0.1.0

The first release of BroadSniff: IR and RF capture for Broadlink universal
remotes, alongside Home Assistant's built-in Broadlink integration.

- Adds capture to every remote set up in the built-in Broadlink
  integration, including remotes added later. Remotes can be left out in
  the options.
- Each remote gets a device linked to the built-in one by MAC address,
  starting in the same area, with IR capture and RF capture switches, a
  Codes captured counter, IR capture and RF capture events, and an IR
  receiver on Home Assistant's infrared platform.
- Settings: a capture window of 1 to 60 seconds (10 by default); a capture
  mode of keep listening and restart the timer after each code (the
  default), keep listening until time runs out, or stop after the first
  code; and an RF frequency of 433.92 MHz (the default), 315 MHz, sweep or
  a custom carrier.
- Captured codes come in the `b64:...` form that `remote.send_command`
  accepts.
- Capture entities follow the built-in integration's availability, and the
  devices of removed or excluded remotes are cleaned up.
- Requires Home Assistant 2026.10 or newer and `broadlink-async` 1.1.0 or
  newer.

BroadSniff started as Broadlink +Capture, a full copy of the Broadlink
integration with capture added. It was never released in that form.
