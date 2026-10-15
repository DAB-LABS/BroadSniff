# Broadlink +Capture

A Home Assistant integration for Broadlink devices that adds capture: put an
RM remote into listening mode from Home Assistant and get the IR and RF codes
it hears, ready to send back or use in automations.

It supports every Broadlink device the built-in Broadlink integration
supports, so you can use it instead of the built-in one for all of your
Broadlink devices.

> **This is a temporary integration.** Home Assistant's infrared platform
> does not yet have a way to represent a receiver that only listens on
> demand, which is how Broadlink devices learn codes. Until it does, this
> integration fills the gap. Once the built-in Broadlink integration supports
> capture, Broadlink +Capture will be sunset and you will be asked to move
> back to the built-in integration. A migration will be provided.

## Capture

Each RM remote gets these entities on top of what the built-in integration
provides:

- **Capture** switch: turn it on and the device listens for infrared codes.
  It turns off by itself when the window closes, and turning it off stops
  the device listening.
- **RF capture** switch (RM pro and RM4 pro): the same for radio frequency.
- **Capture window**: how long a window stays open, 1 to 300 seconds.
- **Stop after first signal**: on by default, so the window closes after one
  code. Turn it off to catch several codes in one window.
- **Sliding window**: each code restarts the countdown, handy for learning a
  whole remote in one go.
- **RF learn frequency** (RM pro and RM4 pro): `0` sweeps for the carrier
  while you hold the button; set it (for example `433.92`) to skip the sweep.
- **IR capture** and **RF capture** events: fire with the captured code in
  the `b64:...` form that `remote.send_command` accepts, and again when a
  window ends, with how many codes it caught.
- **IR receiver**: publishes captured infrared codes on Home Assistant's
  infrared platform, so integrations that listen for infrared can use them.
  Unlike an always-on receiver it only hears codes while a capture window is
  open; its `capturing` attribute shows when that is.

`remote.learn_command` works as in the built-in integration and uses the
same capture windows, so a learn and a capture never run at the same time
on one device.

## Installation

1. In HACS, open the menu, choose **Custom repositories**, and add
   `https://github.com/DAB-LABS/broadlink-capture` as an **Integration**.
2. Install **Broadlink +Capture** and restart Home Assistant.
3. Go to **Settings > Devices & services > Add integration** and choose
   **Broadlink +Capture**.

Requires Home Assistant 2026.10 or newer.

## Using it alongside the built-in Broadlink integration

Both integrations can be installed at the same time, but each device should
be set up in only one of them. Learning mode applies to the whole device, so
a capture in one and a learn in the other at the same moment would get in
each other's way. Both may show the same device as discovered; you can
ignore it in the one you are not using.

Codes learned with the built-in integration are not shared with this one,
and the other way round.

## Devices

The same devices as the built-in integration: RM universal remotes (RM mini,
RM pro, RM4 mini, RM4 pro, RM5 plus and others), smart plugs and power
strips, the A1 and A2 sensors, Hysen thermostats, and LB1/LB2 bulbs.

## Credits

Based on the Broadlink integration that ships with Home Assistant. See
`NOTICE.md`. Device communication uses
[broadlink-async](https://github.com/DAB-LABS/broadlink-async).

## License

Apache License 2.0, the same as Home Assistant. See `LICENSE.md`.
