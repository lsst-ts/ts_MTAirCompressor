# This file is part of ts_MTAirCompressor.
#
# Developed for the Vera Rubin Observatory Telescope and Site Systems.
# This product includes software developed by the LSST Project
# (https://www.lsst.org).
# See the COPYRIGHT file at the top-level directory of this distribution
# for details of code ownership.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

__all__ = ["MTAirCompressorCsc"]

import argparse
import asyncio
from time import monotonic
import typing
import pymodbus.exceptions

from lsst.ts import salobj, utils

from . import __version__
from .aircompressor_model import MTAirCompressorModel, ModbusError
from .config_schema import CONFIG_SCHEMA
from .enums import ErrorCode
from .simulator import create_server


class MTAirCompressorCsc(salobj.ConfigurableCsc):
    """MTAirCompressor CsC

    Parameters
    ----------
    index : `int`
        CSC index.
    config_dir : `str` (optional)
        Directory of configuration files, or None for the standard
        configuration directory (obtained from `get_default_config_dir`).
        This is provided for unit testing.
    initial_state : `lsst.ts.salobj.State`
        CSC initial state.
    override : `str`, optional
        Configuration override file to apply if ``initial_state`` is
        `State.DISABLED` or `State.ENABLED`.
    simulation_mode : `int`
        CSC simulation mode. 0 - no simulation, 1 - software simulation (no mock modbus needed)
    """

    version = __version__
    valid_simulation_modes: typing.Sequence[int] = (0, 1)

    def __init__(
        self,
        index: int,
        config_dir: str = None,
        initial_state=salobj.State.DISABLED,
        override: str = "",
        simulation_mode: int = 0,
    ):
        super().__init__(
            name="MTAirCompressor",
            index=index,
            config_schema=CONFIG_SCHEMA,
            config_dir=config_dir,
            initial_state=initial_state,
            override=override,
            simulation_mode=simulation_mode,
        )

        self.first_run = True
        self.model = None
        self.simulator = None
        self.simulator_future = None
        self._start_by_remote = False
        self._status_update = False
        self._failed_time = None

        self.poll_task = utils.make_done_future()

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Adds custom --hostname, --port and --unit arguments."""
        parser.add_argument(
            "--hostname",
            type=str,
            default=None,
            help="hostname. Unless specified, m1m3cam-aircomp0X.cp.lsst.org, where X is compressor index",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=502,
            help="TCP/IP port. Defaults to 502 (default Modbus TCP/IP port)",
        )
        parser.add_argument(
            "--unit", type=int, default=None, help="modbus unit address"
        )

    @classmethod
    def add_kwargs_from_args(
        cls, args: argparse.Namespace, kwargs: typing.Dict[str, typing.Any]
    ) -> None:
        """Process custom --hostname, --port and --unit arguments."""
        cls.hostname = (
            f"m1m3cam-aircomp{kwargs['index']:02d}.cp.lsst.org"
            if args.hostname is None
            else args.hostname
        )
        cls.port = args.port
        cls.unit = kwargs["index"] if args.unit is None else args.unit

    async def configure(self, config):
        self.config = config

    @staticmethod
    def get_config_pkg():
        return "ts_config_mttcs"

    async def close_tasks(self) -> None:
        await super().close_tasks()
        if self.simulation_mode == 1:
            await self.simulator.shutdown()
            await self.simulator_future.cancel()
        self.poll_task.cancel()
        await self.disconnect()

    async def log_modbus_error(self, modbus_error, msg="", ignore_timeouts=False):
        await self.disconnect()

        if ignore_timeouts is False:
            if self.summary_state != salobj.State.FAULT and (
                self._failed_time is None
                or monotonic() < self._failed_time + self.config.grace_period
            ):
                self.log.error(str(modbus_error))
                if self._failed_time is None:
                    self.log.warning(
                        "Lost compressor connection, will try to reconnect for"
                        f" {self.config.grace_period} seconds"
                    )
                    self._failed_time = monotonic()
                return

        try:
            await self.fault(modbus_error.exception.original_code, msg)
        except AttributeError:
            if isinstance(
                modbus_error.exception, pymodbus.exceptions.ConnectionException
            ):
                await self.fault(ErrorCode.COULD_NOT_CONNECT, msg + str(modbus_error))
            else:
                await self.fault(ErrorCode.MODBUS_ERROR, msg + str(modbus_error))

        self._failed_time = None

    async def connect(self):
        if self.model is None:
            self.model = MTAirCompressorModel(self.hostname, self.port)
        self.model.connect()
        await self.evt_connectionStatus.set_write(connected=True)
        self.log.debug(f"Connected to {self.hostname}:{self.port}")

    async def disconnect(self):
        await self.evt_connectionStatus.set_write(connected=False)
        self.model.close()

    async def end_start(self, data):
        """Enables communication with the compressor."""
        if self.simulation_mode == 1:
            self.hostname = "localhost"
            self.port = 5020
            self.unit = 1

            def run_sim():
                self.simulator = create_server()
                self.simulator.serve_forever()

            self.simulator_future = asyncio.get_running_loop().run_in_executor(
                None, run_sim
            )
            await asyncio.sleep(2)

        try:
            await self.connect()
            if self.poll_task.done():
                self.poll_task = asyncio.create_task(self.poll_loop())
        except ModbusError as er:
            await self.log_modbus_error(er, "Starting up:", True)
            return

    async def end_enable(self, data):
        """Power on compressor after switching to enable state. Raise exception
        if compressor cannot be powered on. Ignore state transition triggered
        by auto update."""
        if self._status_update is True:
            return
        if self._start_by_remote is False:
            raise RuntimeError(
                "Compressor isn't in remote mode - cannot be powered on remotely"
            )

        try:
            self.model.power_on(unit=self.unit)
        except ModbusError as ex:
            await self.log_modbus_error(ex, "Cannot power on compressor")

    async def begin_disable(self, data):
        """Power off compressor before switching to disable state. Ignore state
        transition triggered by auto update."""
        if self._status_update is True:
            return
        try:
            self.model.power_off(self.unit)
        except ModbusError as ex:
            try:
                if ex.exception.original_code & 0x10 == 0x10:
                    raise RuntimeError(
                        "Compressor isn't in remote mode - cannot be powered off"
                    )
            except AttributeError:
                pass
            await self.log_modbus_error(ex, "Cannot power off compressor")

    async def do_reset(self, data):
        """Reset compressor faults."""
        self.model.reset(self.unit)

    async def update_status(self):
        """Read compressor status - 3 status registers starting from address 0x30."""
        status = self.model.get_status(self.unit)

        await self.evt_status.set_write(
            **_statusBits(
                [
                    "readyToStart",
                    "operating",
                    "startInhibit",
                    "motorStartPhase",
                    "offLoad",
                    "onLoad",
                    "softStop",
                    "runOnTimer",
                    "fault",
                    "warning",
                    "serviceRequired",
                    "minAllowedSpeedAchieved",
                    "maxAllowedSpeedAchieved",
                ],
                status[0],
            ),
            **_statusBits(
                [
                    "startByRemote",
                    "startWithTimerControl",
                    "startWithPressureRequirement",
                    "startAfterDePressurise",
                    "startAfterPowerLoss",
                    "startAfterDryerPreRun",
                ],
                status[2],
            ),
        )

        self._start_by_remote = status[2] & 0x01 == 0x01
        self._status_update = True

        if status[0] & 0x02 == 0x02:
            # None can be passed, as begin_enable and begin_disable called from
            # _do_change_state don't care about its content
            if self.summary_state != salobj.State.ENABLED:
                await self.do_enable(None)
                self.log.info("Auto switched to enabled, as compressor is running")
        else:
            if self.summary_state != salobj.State.DISABLED:
                await self.do_disable(None)
                self.log.warning(
                    "Auto switched to disabled, as compressor was powered down"
                )

        self._status_update = False

    async def update_errorsWarnings(self):
        errorsWarnings = self.model.get_error_registers(self.unit)

        await self.evt_errors.set_write(
            **_statusBits(
                [
                    "powerSupplyFailureE400",
                    "emergencyStopActivatedE401",
                    "highMotorTemperatureM1E402",
                    "compressorDischargeTemperatureE403",
                    "startTemperatureLowE404",
                    "dischargeOverPressureE405",
                    "linePressureSensorB1E406",
                    "dischargePressureSensorB2E407",
                    "dischargeTemperatureSensorR2E408",
                    "controllerHardwareE409",
                    "coolingE410",
                    "oilPressureLowE411",
                    "externalFaultE412",
                    "dryerE413",
                    "condensateDrainE414",
                    "noPressureBuildUpE415",
                ],
                errorsWarnings[0],
            ),
            **_statusBits(
                ["heavyStartupE416"],
                errorsWarnings[1],
            ),
            **_statusBits(
                [
                    "preAdjustmentVSDE500",
                    "preAdjustmentE501",
                    "lockedVSDE502",
                    "writeFaultVSDE503",
                    "communicationVSDE504",
                    "stopPressedVSDE505",
                    "stopInputEMVSDE506",
                    "readFaultVSDE507",
                    "stopInputVSDEME508",
                    "seeVSDDisplayE509",
                    "speedBelowMinLimitE510",
                ],
                errorsWarnings[6],
            ),
        )

        await self.evt_warnings.set_write(
            **_statusBits(
                [
                    "serviceDueA600",
                    "dischargeOverPressureA601",
                    "compressorDischargeTemperatureA602",
                    None,
                    None,
                    None,
                    "linePressureHighA606",
                    "controllerBatteryEmptyA607",
                    "dryerA608",
                    "condensateDrainA609",
                    "fineSeparatorA610",
                    "airFilterA611",
                    "oilFilterA612",
                    "oilLevelLowA613",
                    "oilTemperatureHighA614",
                    "externalWarningA615",
                ],
                errorsWarnings[8],
            ),
            **_statusBits(
                [
                    "motorLuricationSystemA616",
                    "input1A617",
                    "input2A618",
                    "input3A619",
                    "input4A620",
                    "input5A621",
                    "input6A622",
                    "fullSDCardA623",
                ],
                errorsWarnings[9],
            ),
            **_statusBits(
                ["temperatureHighVSDA700"],
                errorsWarnings[14],
            ),
        )

    async def update_compressor_info(self):
        """Read compressor info - serial number and software version."""

        def to_string(arr):
            return "".join(map(chr, arr))

        info = self.model.get_compressor_info(self.unit)
        await self.evt_compressorInfo.set_write(
            softwareVersion=to_string(info[0:14]),
            serialNumber=to_string(info[14:23]),
        )

    async def update_analog_data(self):
        """Read compressor analog (telemetry-worth) data."""
        analog = self.model.get_analog_data(self.unit)

        await self.tel_analogData.set_write(
            force_output=True,
            waterLevel=analog[0],
            targetSpeed=analog[1],
            motorCurrent=analog[2] / 10.0,
            heatsinkTemperature=analog[3],
            dclinkVoltage=analog[4],
            motorSpeedPercentage=analog[5],
            motorSpeedRPM=analog[6],
            motorInput=analog[7] / 10.0,
            compressorPowerConsumption=analog[8] / 10.0,
            compressorVolumePercentage=analog[9],
            compressorVolume=analog[10] / 10.0,
            groupVolume=analog[11] / 10.0,
            stage1OutputPressure=analog[12],
            linePressure=analog[13],
            stage1OutputTemperature=analog[14],
        )

    async def update_timer(self):
        """Read compressors timers."""
        timer = self.model.get_timers(self.unit)

        def to_64(a):
            return a[0] << 16 | a[1]

        await self.evt_timerInfo.set_write(
            runningHours=to_64(timer[0:2]),
            loadedHours=to_64(timer[2:4]),
            lowestServiceCounter=timer[4],
            runOnTimer=timer[5],
            loadedHours50Percent=to_64(timer[6:8]),
        )

    async def telemetry_loop(self):
        """Runs telemetry loop."""
        timerUpdate = 0
        try:
            while True:
                await self.update_status()
                await self.update_errorsWarnings()
                await self.update_analog_data()

                if self.first_run:
                    await self.update_compressor_info()
                    self.first_run = False

                if timerUpdate <= 0:
                    await self.update_timer()
                    timerUpdate = 60
                else:
                    timerUpdate -= 1

                await asyncio.sleep(1)

        except ModbusError as ex:
            await self.log_modbus_error(ex)

        except Exception as ex:
            await self.fault(1, f"Error in telemetry loop: {str(ex)}")

    async def poll_loop(self):
        while True:
            try:
                if self._failed_time is not None:
                    if self.model is not None:
                        try:
                            await self.connect()
                            self.model.get_status(self.unit)
                            self.log.info(
                                "Compressor connection is back after "
                                f"{monotonic() - self._failed_time:.1f} seconds"
                            )
                            self._failed_time = None
                        except ModbusError as er:
                            await self.log_modbus_error(er, "While reconnecting:")
                            await asyncio.sleep(5)
                            continue
                elif self.disabled_or_enabled:
                    await self.telemetry_loop()
                elif self.summary_state in (salobj.State.STANDBY, salobj.State.FAULT):
                    pass
                else:
                    self.log.critical(f"Unhandled state: {self.summary_state}")

                await asyncio.sleep(1)
            except Exception as ex:
                self.log.exception(f"Exception in poll loop: {str(ex)}")
                await asyncio.sleep(2)

            if self.summary_state == salobj.State.FAULT:
                raise RuntimeError("In FAULT state")


def _statusBits(fields, value):
    """Helper function. Converts value bits into boolean fields.

    Parameters
    ----------
    fields : [`str`]
        Name of fields to extract. Corresponds to bits in value, with lowest
        (0x0001) first. Can be None to specify this bit doesn't have any
        meaning.
    value : `int`
        Bit-masked value. Bits corresponds to named values in fields.

    Returns
    -------
    bits : {`str` : `bool`}
        Map where keys are values passed in fields and values are booleans
        corresponding to whenever that bit is set.
    """
    ret = {}
    for f in fields:
        if f is not None:
            ret[f] = value & 0x0001
        value >>= 1
    return ret
