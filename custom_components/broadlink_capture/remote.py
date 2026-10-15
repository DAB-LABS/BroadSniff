"""Support for Broadlink remotes."""

import asyncio
from base64 import b64encode
from collections import defaultdict
from collections.abc import Iterable
from datetime import timedelta
from itertools import product
import logging
from typing import Any, override

from broadlink_async.exceptions import (
    AuthorizationError,
    BroadlinkException,
    NetworkTimeoutError,
)
from broadlink_async.remote import CapturedSignal
import probatio

from homeassistant.components import persistent_notification
from homeassistant.components.remote import (
    ATTR_ALTERNATIVE,
    ATTR_COMMAND_TYPE,
    ATTR_DELAY_SECS,
    ATTR_DEVICE,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    DOMAIN as RM_DOMAIN,
    SERVICE_DELETE_COMMAND,
    SERVICE_LEARN_COMMAND,
    SERVICE_SEND_COMMAND,
    RemoteEntity,
    RemoteEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_COMMAND, STATE_OFF
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.storage import Store

from .capture import CaptureKind
from .const import DOMAIN
from .entity import BroadlinkEntity
from .helpers import data_packet

_LOGGER = logging.getLogger(__name__)

LEARNING_TIMEOUT = timedelta(seconds=30)

COMMAND_TYPE_IR = "ir"
COMMAND_TYPE_RF = "rf"
COMMAND_TYPES = [COMMAND_TYPE_IR, COMMAND_TYPE_RF]

CODE_STORAGE_VERSION = 1
FLAG_STORAGE_VERSION = 1

CODE_SAVE_DELAY = 15
FLAG_SAVE_DELAY = 15

COMMAND_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_COMMAND): probatio.All(
            probatio.EnsureList(),
            [probatio.All(cv.string, probatio.Length(min=1))],
            probatio.Length(min=1),
        ),
    },
    extra=probatio.ALLOW_EXTRA,
)

SERVICE_SEND_SCHEMA = COMMAND_SCHEMA.extend(
    {
        probatio.Optional(ATTR_DEVICE): probatio.All(cv.string, probatio.Length(min=1)),
        probatio.Optional(ATTR_DELAY_SECS, default=DEFAULT_DELAY_SECS): probatio.Coerce(
            float
        ),
    }
)

SERVICE_LEARN_SCHEMA = COMMAND_SCHEMA.extend(
    {
        probatio.Required(ATTR_DEVICE): probatio.All(cv.string, probatio.Length(min=1)),
        probatio.Optional(ATTR_COMMAND_TYPE, default=COMMAND_TYPE_IR): probatio.In(
            COMMAND_TYPES
        ),
        probatio.Optional(ATTR_ALTERNATIVE, default=False): cv.boolean,
    }
)

SERVICE_DELETE_SCHEMA = COMMAND_SCHEMA.extend(
    {probatio.Required(ATTR_DEVICE): probatio.All(cv.string, probatio.Length(min=1))}
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a Broadlink remote."""
    # Uses legacy hass.data[DOMAIN] pattern
    # pylint: disable-next=home-assistant-use-runtime-data
    device = hass.data[DOMAIN].devices[config_entry.entry_id]
    remote = BroadlinkRemote(
        device,
        Store(hass, CODE_STORAGE_VERSION, f"{DOMAIN}_remote_{device.unique_id}_codes"),
        Store(hass, FLAG_STORAGE_VERSION, f"{DOMAIN}_remote_{device.unique_id}_flags"),
    )
    async_add_entities([remote], False)


class BroadlinkRemote(BroadlinkEntity, RemoteEntity, RestoreEntity):
    """Representation of a Broadlink remote."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, device, codes, flags):
        """Initialize the remote."""
        super().__init__(device)
        self._code_storage = codes
        self._flag_storage = flags
        self._storage_loaded = False
        self._codes = {}
        self._flags = defaultdict(int)
        self._lock = asyncio.Lock()

        self._attr_is_on = True
        self._attr_supported_features = (
            RemoteEntityFeature.LEARN_COMMAND | RemoteEntityFeature.DELETE_COMMAND
        )
        self._attr_unique_id = device.unique_id

    def _extract_codes(self, commands, device=None):
        """Extract a list of codes.

        If the command starts with `b64:`, extract the code from it.
        Otherwise, extract the code from storage, using the command and
        device as keys.

        The codes are returned in sublists. For toggle commands, the
        sublist contains two codes that must be sent alternately with
        each call.
        """
        code_list = []
        for cmd in commands:
            if cmd.startswith("b64:"):
                codes = [cmd[4:]]

            else:
                if device is None:
                    raise ValueError("You need to specify a device")

                try:
                    codes = self._codes[device][cmd]
                except KeyError as err:
                    raise ValueError(f"Command not found: {cmd!r}") from err

                if isinstance(codes, list):
                    codes = codes[:]
                else:
                    codes = [codes]

            for idx, code in enumerate(codes):
                try:
                    codes[idx] = data_packet(code)
                except ValueError as err:
                    raise ValueError(f"Invalid code: {code!r}") from err

            code_list.append(codes)
        return code_list

    @callback
    def _get_codes(self):
        """Return a dictionary of codes."""
        return self._codes

    @callback
    def _get_flags(self):
        """Return a dictionary of toggle flags.

        A toggle flag indicates whether the remote should send an
        alternative code.
        """
        return self._flags

    @override
    async def async_added_to_hass(self) -> None:
        """Call when the remote is added to hass."""
        state = await self.async_get_last_state()
        self._attr_is_on = state is None or state.state != STATE_OFF
        await super().async_added_to_hass()

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the remote."""
        self._attr_is_on = True
        self.async_write_ha_state()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the remote."""
        self._attr_is_on = False
        self.async_write_ha_state()

    async def _async_load_storage(self):
        """Load code and flag storage from disk."""
        # Exception is intentionally not trapped to
        # provide feedback if something fails.
        self._codes.update(await self._code_storage.async_load() or {})
        self._flags.update(await self._flag_storage.async_load() or {})
        self._storage_loaded = True

    @override
    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send a list of commands to a device."""
        kwargs[ATTR_COMMAND] = command
        kwargs = SERVICE_SEND_SCHEMA(kwargs)
        commands = kwargs[ATTR_COMMAND]
        subdevice = kwargs.get(ATTR_DEVICE)
        repeat = kwargs[ATTR_NUM_REPEATS]
        delay = kwargs[ATTR_DELAY_SECS]
        service = f"{RM_DOMAIN}.{SERVICE_SEND_COMMAND}"
        device = self._device

        if not self._attr_is_on:
            _LOGGER.warning(
                "%s canceled: %s entity is turned off", service, self.entity_id
            )
            return

        if not self._storage_loaded:
            await self._async_load_storage()

        try:
            code_list = self._extract_codes(commands, subdevice)
        except ValueError as err:
            _LOGGER.error("Failed to call %s: %s", service, err)
            raise

        rf_flags = {0xB2, 0xD7}
        if not hasattr(device.api, "sweep_frequency") and any(
            c[0] in rf_flags for codes in code_list for c in codes
        ):
            err_msg = f"{self.entity_id} doesn't support sending RF commands"
            _LOGGER.error("Failed to call %s: %s", service, err_msg)
            raise ValueError(err_msg)

        at_least_one_sent = False
        for _, codes in product(range(repeat), code_list):
            if at_least_one_sent:
                await asyncio.sleep(delay)

            if len(codes) > 1:
                code = codes[self._flags[subdevice]]
            else:
                code = codes[0]

            try:
                await device.async_request(device.api.send_data, code)
            # pylint: disable-next=home-assistant-action-swallowed-exception
            except (BroadlinkException, OSError) as err:
                _LOGGER.error("Error during %s: %s", service, err)
                break

            if len(codes) > 1:
                self._flags[subdevice] ^= 1
            at_least_one_sent = True

        if at_least_one_sent:
            self._flag_storage.async_delay_save(self._get_flags, FLAG_SAVE_DELAY)

    @override
    async def async_learn_command(self, **kwargs: Any) -> None:
        """Learn a list of commands from a remote."""
        kwargs = SERVICE_LEARN_SCHEMA(kwargs)
        commands = kwargs[ATTR_COMMAND]
        command_type = kwargs[ATTR_COMMAND_TYPE]
        subdevice = kwargs[ATTR_DEVICE]
        toggle = kwargs[ATTR_ALTERNATIVE]
        service = f"{RM_DOMAIN}.{SERVICE_LEARN_COMMAND}"
        device = self._device

        if not self._attr_is_on:
            _LOGGER.warning(
                "%s canceled: %s entity is turned off", service, self.entity_id
            )
            return

        if not self._storage_loaded:
            await self._async_load_storage()

        async with self._lock:
            if command_type == COMMAND_TYPE_IR:
                learn_command = self._async_learn_ir_command

            elif hasattr(device.api, "sweep_frequency"):
                learn_command = self._async_learn_rf_command

            else:
                err_msg = f"{self.entity_id} doesn't support learning RF commands"
                _LOGGER.error("Failed to call %s: %s", service, err_msg)
                raise ValueError(err_msg)

            should_store = False

            for command in commands:
                try:
                    first = await learn_command(command)
                    code: str | list[str] = first
                    if toggle:
                        code = [first, await learn_command(command)]

                # pylint: disable-next=home-assistant-action-swallowed-exception
                except (AuthorizationError, NetworkTimeoutError, OSError) as err:
                    _LOGGER.error("Failed to learn '%s': %s", command, err)
                    break

                except BroadlinkException as err:
                    _LOGGER.error("Failed to learn '%s': %s", command, err)
                    continue

                # pylint: disable-next=home-assistant-action-swallowed-exception
                except HomeAssistantError as err:
                    _LOGGER.error("Failed to learn '%s': %s", command, err)
                    if isinstance(
                        err.__cause__,
                        (AuthorizationError, NetworkTimeoutError, OSError),
                    ):
                        break
                    continue

                self._codes.setdefault(subdevice, {}).update({command: code})
                should_store = True

            if should_store:
                await self._code_storage.async_save(self._codes)

    async def _async_learn_ir_command(self, command: str) -> str:
        """Learn an infrared command."""
        return await self._async_learn(
            command, CaptureKind.IR, prompt=f"Press the '{command}' button."
        )

    async def _async_learn_rf_command(self, command: str) -> str:
        """Learn a radiofrequency command.

        With the RF learn frequency set, the device listens on that carrier
        right away. Without it the device sweeps for the carrier while the
        button is held, then learns the code from a fresh press; the library
        runs both phases inside one capture window.
        """
        if self._device.capture.rf_frequency:
            prompt = f"Press the '{command}' button."
        else:
            prompt = (
                f"Press and hold the '{command}' button for a few seconds, "
                "then release it and press it once more."
            )
        return await self._async_learn(command, CaptureKind.RF, prompt=prompt)

    async def _async_learn(
        self, command: str, kind: CaptureKind, *, prompt: str
    ) -> str:
        """Open a capture window on the device and return the first code."""
        received: list[CapturedSignal] = []
        window = await self._device.capture.async_start(
            kind,
            window=LEARNING_TIMEOUT.total_seconds(),
            stop_after_first=True,
            sliding=False,
            on_signal=received.append,
        )

        persistent_notification.async_create(
            self.hass,
            prompt,
            title="Learn command",
            notification_id="learn_command",
        )

        try:
            await window
        finally:
            persistent_notification.async_dismiss(
                self.hass, notification_id="learn_command"
            )

        if not received:
            band = "radiofrequency" if kind is CaptureKind.RF else "infrared"
            raise TimeoutError(
                f"No {band} code received within "
                f"{LEARNING_TIMEOUT.total_seconds()} seconds"
            )
        return b64encode(received[0].packet).decode("utf8")

    @override
    async def async_delete_command(self, **kwargs: Any) -> None:
        """Delete a list of commands from a remote."""
        kwargs = SERVICE_DELETE_SCHEMA(kwargs)
        commands = kwargs[ATTR_COMMAND]
        subdevice = kwargs[ATTR_DEVICE]
        service = f"{RM_DOMAIN}.{SERVICE_DELETE_COMMAND}"

        if not self._attr_is_on:
            _LOGGER.warning(
                "%s canceled: %s entity is turned off",
                service,
                self.entity_id,
            )
            return

        if not self._storage_loaded:
            await self._async_load_storage()

        try:
            codes = self._codes[subdevice]
        except KeyError as err:
            err_msg = f"Device not found: {subdevice!r}"
            _LOGGER.error("Failed to call %s. %s", service, err_msg)
            raise ValueError(err_msg) from err

        cmds_not_found = []
        for command in commands:
            try:
                del codes[command]
            except KeyError:
                cmds_not_found.append(command)

        if cmds_not_found:
            if len(cmds_not_found) == 1:
                err_msg = f"Command not found: {cmds_not_found[0]!r}"
            else:
                err_msg = f"Commands not found: {cmds_not_found!r}"

            if len(cmds_not_found) == len(commands):
                _LOGGER.error("Failed to call %s. %s", service, err_msg)
                raise ValueError(err_msg)

            _LOGGER.error("Error during %s. %s", service, err_msg)

        # Clean up
        if not codes:
            del self._codes[subdevice]
            if self._flags.pop(subdevice, None) is not None:
                self._flag_storage.async_delay_save(self._get_flags, FLAG_SAVE_DELAY)

        self._code_storage.async_delay_save(self._get_codes, CODE_SAVE_DELAY)
