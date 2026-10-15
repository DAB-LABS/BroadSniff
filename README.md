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
