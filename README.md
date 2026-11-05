<p align="center">
  <img src="https://raw.githubusercontent.com/DAB-LABS/BroadSniff/main/images/BroadSniff-header.png" alt="BroadSniff logo: a hockey puck remote hub wearing a chestnut pompadour, peeking up behind a ribbon banner reading BROAD·SNIFF" width="720" />
</p>

# BroadSniff

BroadSniff lets Home Assistant hear what your Broadlink universal remote
hears. Turn on capture, point any IR or RF remote at your Broadlink RM,
press buttons, and BroadSniff hands you each code: ready to send back, to
trigger automations, or to pass to [HAIR](https://github.com/DAB-LABS/HAIR)
and anything else that listens on Home Assistant's infrared or radio
frequency platform.

It is a small custom integration that works alongside Home Assistant's
built-in Broadlink integration. Your remotes stay set up there; BroadSniff
only adds capture.

> [!NOTE]
> RF codes reach other integrations through BroadSniff's RF receiver, which
> needs Home Assistant 2026.11 or newer. On 2026.10, RF codes stay with
> your Broadlink: you can read them, trigger automations from them while a
> capture window is open, and send them back through an RM pro or RM4 pro
> with `remote.send_command`.

## Install

### HACS (recommended)

[![Open your Home Assistant instance and open the BroadSniff repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=DAB-LABS&repository=broadsniff&category=integration)

Click the button above. If HACS asks, confirm adding the repository. Then
click **Download** and restart Home Assistant.

Or add it by hand:

1. Open **HACS** in your Home Assistant sidebar.
2. Open the menu (three dots, top right) and choose **Custom
   repositories**.
3. Add `https://github.com/DAB-LABS/broadsniff` with the type
   **Integration**.
4. Find **BroadSniff** in HACS, click **Download**, and restart Home
   Assistant.

### Manual

1. Download the latest release from the
   [releases page](https://github.com/DAB-LABS/broadsniff/releases).
2. Copy `custom_components/broadsniff` into the `custom_components` folder
   of your Home Assistant configuration.
3. Restart Home Assistant.

### Add the integration

[![Open your Home Assistant instance and start setting up BroadSniff.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=broadsniff)

Click the button above, or go to **Settings > Devices & services > Add
integration** and choose **BroadSniff**.

One setup covers every remote in the Broadlink integration, including ones
you add later. To leave a remote out, open **Configure** on BroadSniff and
pick it.

### Requirements

- Home Assistant 2026.10 or newer.
- At least one Broadlink universal remote set up in the built-in Broadlink
  integration: RM mini, RM pro, RM4 mini, RM4 pro, RM5 plus and similar.
  RF capture needs an RM pro or RM4 pro.
- The RF receiver needs Home Assistant 2026.11 or newer.

## Why BroadSniff exists

Broadlink RM remotes can learn codes, but only in a learning mode: you
ask the remote to listen, it waits for one press, and then it stops. They
cannot listen all the time.

Home Assistant's built-in Broadlink integration sends IR and RF codes well,
and it can learn codes with `remote.learn_command`, which stores them for
that one remote. What it cannot do is hand a captured code to the rest of
Home Assistant: there is no event when a code arrives, and nothing on the
infrared platform for other integrations to listen to.

The reason is architectural. Home Assistant's infrared platform has a
receiver entity, but it is designed for receivers that are always
listening, like an ESPHome board with an IR receiver. A device that only
listens when asked, as every Broadlink remote does, has no proper place in
it yet. Until it does, a capture feature does not belong in the built-in
integration.

BroadSniff fills that gap from the outside. It opens a capture window on
request, publishes every code it catches as an event, and offers an IR
receiver on the infrared platform that hears codes while a window is open.

## Where this is going

BroadSniff is meant to be temporary. A proposal is in progress for Home
Assistant's infrared platform to let a receiver say that it captures on
demand rather than listening all the time, with the capture action built
into the receiver itself. If that lands, capture can move into the
built-in Broadlink integration, and BroadSniff will be retired.

The same applies to RF. Home Assistant's radio frequency platform has
receivers from 2026.11, and BroadSniff publishes its RF captures there the
way it publishes IR captures on the infrared platform.

Retiring it will be easy. BroadSniff never changes your Broadlink setup,
so removing it leaves your remotes and the built-in integration exactly as
they are. You would only need to point automations and HAIR at the new
entities.

BroadSniff and the built-in integration use the same library to talk to
the remote, [broadlink-async](https://github.com/DAB-LABS/broadlink-async),
from Home Assistant 2026.10.2 on.

## What you get

Home Assistant gives each integration its own device. BroadSniff adds a
device with the same name as your remote, in the same area, and Home
Assistant shows the two as linked devices because they share the remote's
MAC address. Open it from the remote's device page or from
**Settings > Devices & services > BroadSniff**.

Each remote gets:

- **IR capture**: a switch. Turn it on and the remote listens for infrared
  codes. Its icon changes while it listens. It turns off by itself when the
  window closes, and turning it off stops the remote listening.
- **RF capture** (RM pro and RM4 pro): the same for radio frequency.
- **Codes captured**: counts the codes the open window has caught, so you
  can watch it go up as you press buttons. It goes back to zero when a new
  window opens and keeps the total once the window closes.
- **IR capture** and **RF capture** events: fire with each code, and again
  when a window closes with how many codes it caught.
- **IR receiver**: publishes captured infrared codes on Home Assistant's
  infrared platform. It only hears codes while a capture window is open;
  its `capturing` attribute shows when that is, and its other attributes
  say how to open one (see [Use it with HAIR](#use-it-with-hair)). While
  the remote does not answer, its BroadSniff entities are unavailable and
  capture cannot be turned on. BroadSniff notices on its own: when a
  request goes unanswered it asks again a few seconds later, and if that
  goes unanswered too it takes the remote offline and checks back every
  15 seconds.
- **RF receiver** (RM pro and RM4 pro): publishes captured RF codes on
  Home Assistant's radio frequency platform, each with the carrier it was
  heard on. It needs Home Assistant 2026.11 or newer, the first version
  with that platform; on older versions BroadSniff runs without it and
  everything else works. Like the IR receiver, it only hears codes while
  an **RF capture** window is open, and it has the same attributes, for RF
  windows (see [The RF receiver](#the-rf-receiver)).

And these settings, which are remembered across restarts:

- **Capture window**: how long a window stays open, 1 to 60 seconds.
  10 by default.
- **Capture mode**:
  - **Keep listening, restart timer after each code** (default). Each code
    restarts the countdown, so you can capture a whole remote in one go.
    With the default window, it closes 10 seconds after your last press.
  - **Keep listening until time runs out**: catches several codes in a
    window of fixed length.
  - **Stop after the first code**: closes as soon as it hears one code.
- **RF frequency** (RM pro and RM4 pro): the carrier RF capture listens on.
  - **433.92 MHz** (default): most remotes.
  - **315 MHz**: many US garage and gate remotes.
  - **Sweep**: the remote searches for the carrier. Hold the button for a
    few seconds, release it, then press it once more. Slower, and less
    reliable on some firmware. It needs a remote that keeps transmitting
    while the button is held (see [Capture an RF code](#capture-an-rf-code)).
  - **Custom**: uses **RF custom frequency**, 300 to 450 MHz. That entity
    is disabled until you need it; enable it on the device page.

The capture entities follow the built-in integration: when Broadlink has a
remote unavailable, so are its capture entities.

## How to use it

### Capture an IR code

1. Open your remote's BroadSniff device.
2. Turn on **IR capture**.
3. Point the remote you want to capture at the Broadlink and press a
   button.
4. Watch **Codes captured** go to 1. The **IR capture** event now holds the
   code, in its `code` attribute.

To read the code, open the **IR capture** event, choose the
three-dot menu, and look at **Attributes**, or find it in
**Developer tools > States**.

### Capture a whole remote

Leave **Capture mode** on **Keep listening, restart timer after each
code**, turn on **IR capture**, and press the buttons one after another.
Each press adds one to **Codes captured** and restarts the countdown. When
you stop pressing, the window closes 10 seconds later. Every code is in
the history of the **IR capture** event.

### Capture an RF code

Turn on **RF capture** and press the button on your RF remote. Most
remotes use 433.92 MHz, the default. If nothing arrives, try 315 MHz, or
**Sweep** if you do not know the frequency. The **RF capture** event holds
the code and the carrier it was heard on, in `frequency_mhz`.

Leave a few seconds between presses. After each code the remote has to
start listening again, and a press that comes too soon after the last one
can be missed.

With **Sweep**, hold the button down while the remote searches. The RF
receiver's `capture_phase` reads `sweeping` until the carrier locks, then
`listening`: release the button and press it once more. If the carrier
does not lock before the window runs out, the window closes and the **RF
capture** event's `window_ended` says `reason: no_lock`. Turning **RF
capture** off stops a sweep.

Sweep only works with a remote that keeps transmitting while its button
is held. Many keyfobs send one short burst and stop, even with the button
still down, so the remote never finds the carrier. For those, pick the
band in **RF frequency** instead and keep Sweep as the fallback.

### Send a code back

Codes come in the `b64:...` form that the built-in integration's
`remote.send_command` accepts, for IR and RF alike:

```yaml
action: remote.send_command
target:
  entity_id: remote.living_room
data:
  command: "b64:JgBQAAAB..."
```

### Use a capture in an automation

The event entities change state every time they fire, and `event_type`
says what happened: `signal` for a code, `window_ended` when a window
closes.

```yaml
triggers:
  - trigger: state
    entity_id: event.living_room_ir_capture
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.attributes.event_type == 'signal' }}"
actions:
  - action: notify.persistent_notification
    data:
      message: "Captured {{ trigger.to_state.attributes.code }}"
```

### Use it with HAIR

[HAIR](https://github.com/DAB-LABS/HAIR) listens on Home Assistant's
infrared platform, so it sees BroadSniff's **IR receiver** like any other.
The receiver also follows HAIR's
[windowed-receiver contract](https://github.com/DAB-LABS/HAIR/blob/main/docs/windowed-receivers.md),
which tells HAIR that it captures on demand and how to open a window, so
from HAIR 0.18.0 on, HAIR turns on **IR capture** for you when you listen
from it. The receiver's attributes:

- `capturing`: true while an IR capture window is open, false otherwise.
- `capture_switch`: the entity ID of the remote's **IR capture** switch,
  kept current if you rename it. It is left out while that switch is
  disabled.
- `window_ends_at`: when the open window closes, or null.
- `capture_mode`: `single`, `sliding` or `multiple`, the **Capture mode**
  setting for the next window.

### The RF receiver

On an RM pro or RM4 pro with Home Assistant 2026.11 or newer, the **RF
receiver** publishes RF captures on Home Assistant's radio frequency
platform, with the carrier each one was heard on. It has the same
attributes as the IR receiver, for **RF capture** windows, and names the
**RF capture** switch, plus two that say how an RF window is getting on:

- `capture_phase`: `sweeping` while the remote searches for the carrier,
  `listening` once it is listening for a code, null with no RF window open.
- `frequency_hz`: the carrier the open RF window listens on, in Hz, once it
  is known.

It declares the two bands the built-in Broadlink integration sends on,
433.05 to 434.79 MHz and 314.95 to 315.25 MHz.

**Reading an RM4 pro capture.** BroadSniff publishes every duration the
remote reports, as it reports them. An RF capture from an RM4 pro is a
read of the remote's buffer, cut at 500 durations (1,000 for a long
press), and the buffer has a seam every 250 durations:

- The last value is where the read stopped, not part of the code. On the
  bench it is always 45,680 us, whatever was pressed.
- The duration at every 250th position (250, 500, 750, counting from 1)
  can be stretched. On a keyfob the space at position 250 came back about
  4.5 ms long instead of about 1.25 ms, and the frame around it can decode
  as a different code.

Anything that decodes these captures should drop the last value and not
trust the duration at every 250th position. A capture holds several
repeats of the code, so the clean frames between the seams are enough to
read it.

## Good to know

- BroadSniff keeps its own connection to each remote for capture. The
  built-in integration keeps its connection for everything else.
- Learning mode applies to the whole remote. Avoid running
  `remote.learn_command` from the built-in integration while a capture
  window is open on the same remote.
- While a window is open, the Broadlink hears everything in range,
  including codes it sends itself. If something sends through the same
  Broadlink during a capture, for example an automation or a HAIR remote
  that repeats what it hears, you may capture that too.
- A remote that is locked in the Broadlink app can fail to authenticate,
  for the built-in integration and BroadSniff alike. Unlock it in the
  app if capture reports an error.

## Removing

Delete BroadSniff under **Settings > Devices & services** and remove it
from HACS. Its devices and entities go with it. Your remotes and the
built-in Broadlink integration stay as they are.

## Credits

Device communication uses
[broadlink-async](https://github.com/DAB-LABS/broadlink-async). Thanks to
the authors and code owners of Home Assistant's Broadlink integration.

BroadSniff is not affiliated with or endorsed by Broadlink.

## License

Apache License 2.0, the same as Home Assistant. See `LICENSE.md`.
