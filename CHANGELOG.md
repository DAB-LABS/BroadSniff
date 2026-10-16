# Changelog

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
