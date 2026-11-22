# Changelog

## 0.3.2 - Unreleased

- The RF receiver declares the bands an RM4 pro was measured receiving on,
  305 to 335 MHz and 433.42 to 438 MHz, instead of the built-in
  integration's two narrow transmit bands. A carrier outside them can still
  be set.
- **RF frequency** has a 433.42 MHz preset, for Somfy blinds and awnings.
- **RF custom frequency** takes 305 to 438 MHz, the span of the measured
  bands, instead of 300 to 450. A value saved outside it comes back as the
  nearest end.
- The RF receiver names the **RF custom frequency** number as
  `frequency_number`, with `frequency_number_option: custom`, while that
  number is enabled, so HAIR can offer any carrier. `frequency_options`
  still gives Custom's carrier in Hz, so older HAIR keeps working.

## 0.3.1 - 2026-11-20

- Both receivers name the entities that set the next window, as HAIR's
  windowed-receiver contract describes, so HAIR can offer them as pickers
  on the receiver's pill: `window_length` (the **Capture window** number)
  and `capture_mode_select` (the **Capture mode** select). The RF receiver
  also names the **RF frequency** select as `frequency_select`, with
  `frequency_options` saying what each option means: 433.92 MHz, 315 MHz,
  sweep, and the **RF custom frequency** carrier while that number is
  enabled. Each name follows a rename and is left out while its entity is
  disabled.
- The capture event keeps showing the code after its window closes. The
  `window_ended` event replaced the code on the event entity, so a code
  captured without HAIR seemed to vanish (#7). `window_ended` now repeats
  the window's last code, and `frequency_mhz` for RF; a window that caught
  nothing still has no code. The README says that BroadSniff passes codes
  on and does not store them, and how to keep them.

## 0.3.0 - 2026-11-10

- An RF receiver on Home Assistant's radio frequency platform, for the RM
  pro and RM4 pro, from Home Assistant 2026.11. It publishes every code an
  RF capture window catches, with the carrier it was heard on: the one an
  RM4 pro records in its capture, otherwise the one the window listened
  on. Every duration the remote reports is kept. On an RM4 pro the last
  value of a capture is where the read of the remote's buffer stopped,
  and the duration at every 250th position can be stretched by a seam in
  that buffer; the README explains how to read around both. It declares
  the two bands the built-in Broadlink integration sends on. On Home
  Assistant 2026.10 BroadSniff loads without it.
- The RF receiver follows the windowed-receiver contract like the IR
  receiver, for RF windows: `capturing`, `capture_switch` (the RF capture
  switch), `window_ends_at` and `capture_mode`. Two more attributes say how
  an RF window is getting on: `capture_phase`, `sweeping` or `listening`,
  and `frequency_hz`, the carrier it listens on.
- A sweep is now visible and can be stopped. While the remote searches for
  the carrier the window reads `sweeping`; once the carrier locks it reads
  `listening` and names the carrier. A sliding window starts its full
  length again at the lock. A sweep that does not lock before the window
  runs out closes the window with `reason: no_lock` on the RF capture
  event, and turning RF capture off during a sweep takes the remote out of
  it. Sweep needs a remote that keeps transmitting while the button is
  held; for a keyfob that sends one short burst, pick the band instead.
- BroadSniff is in the HACS default list, so it installs without adding
  a custom repository. The README's install steps say so.

## 0.2.1 - 2026-10-19

- BroadSniff has its own icon and logo, shown in Home Assistant and HACS.
- The README opens with the BroadSniff header.
- The HACS validation runs every check again, brands included.

## 0.2.0 - 2026-10-19

- The IR receiver follows HAIR's windowed-receiver contract, so HAIR, or
  any integration that reads the contract, can open a capture window
  itself. Two attributes join `capturing` and `window_ends_at`:
  `capture_switch`, the entity ID of the remote's IR capture switch, and
  `capture_mode`, the capture mode the next window opens in. The switch
  is looked up by its unique ID each time, so renaming it is picked up at
  once, and it is left out while the switch is disabled. A capture mode
  change now reaches the receiver straight away.
- A remote that stops answering goes offline in BroadSniff too. Its
  entities follow the built-in Broadlink integration's entities on the
  same device, so an unplugged remote's capture entities go unavailable
  with them and come back with them. Turning on capture asks the remote
  first and fails with an error if it does not answer, instead of opening
  a window that cannot hear anything. A window whose remote stops
  answering now ends within a few seconds, with a warning in the log,
  where it used to run out its time as if nothing had been pressed.
- BroadSniff does not wait for the built-in integration, which takes
  about three minutes to give up on an unplugged remote. When its own
  request goes unanswered it asks once more a few seconds later, so a
  single dropped exchange on Wi-Fi changes nothing; if that goes
  unanswered too, the remote's entities go unavailable, and BroadSniff
  asks again every 15 seconds until the remote answers or the built-in
  integration says it is back. A refused turn-on takes under two seconds
  and says the remote appears disconnected.
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
