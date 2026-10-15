# Changelog

Versions follow the Home Assistant release the integration is based on.

## Unreleased

Starts from the Broadlink source in Home Assistant 2026.10.1 plus
home-assistant/core#186053 (the switch to `broadlink-async`).

- The integration is renamed to the `broadlink_capture` domain, named
  Broadlink +Capture, so it installs next to the built-in Broadlink
  integration instead of replacing it.
- Learned codes are stored under `broadlink_capture_remote_<id>_codes` and
  `_flags`, separate from the built-in integration's storage, so a device
  set up in both cannot overwrite the other's codes.
- Requires `broadlink-async>=1.1.0` instead of an exact pin, so it uses
  whatever version the built-in integration has installed.
- English translations ship as `translations/en.json`, resolved from core's
  `strings.json`.
- Tests are core's Broadlink tests, run against the renamed integration with
  `pytest-homeassistant-custom-component`.
