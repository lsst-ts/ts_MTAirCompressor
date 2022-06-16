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
import typing
from lsst.ts import salobj, utils

from . import __version__
from .aircompressor_model import MTAirCompressorModel, ModbusError
from .simulator import create_server


class MTAirCompressorCsc(salobj.BaseCsc):
    """MTAirCompressor CsC

    Parameters
    ----------
    index : `int`
        CSC index.
    initial_state : `lsst.ts.salobj.State`
        CSC initial state.
    simulation_mode : `int`
        CSC simulation mode. 0 - no simulation, 1 - software simulation (no mock modbus needed)
    """

    version = __version__
    valid_simulation_modes: typing.Sequence[int] = (0, 1)

    def __init__(
        self, index: int, initial_state=salobj.State.DISABLED, simulation_mode: int = 0
    ):
        super().__init__(
            name="MTAirCompressor",
            index=index,
            simulation_mode=simulation_mode,
            initial_state=initial_state,
        )

        self.first_run = True
        self.model = None
        self.simulator = None
        self.simulator_future = None
        self.telemetry_task = utils.make_done_future()

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

    async def close_tasks(self) -> None:
        if self.simulation_mode == 1:
            await self.simulator.shutdown()
            await self.simulator_future.cancel()
        await super().close_tasks()

    async def begin_start(self, data):
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

        if self.model is None:
            self.model = MTAirCompressorModel(self.hostname, self.port)
            self.model.connect()

        if self.telemetry_task is None or self.telemetry_task.done():
            self.telemetry_task = asyncio.create_task(self.telemetry_loop())
        self.log.debug(f"Connected to {self.hostname}:{self.port}")

    async def end_enable(self, data):
        """Power on compressor after switching to enable state. Raise exception
        if compressor cannot be powered on."""
        if self._ready_to_start is False:
            return

        try:
            self.model.power_on(unit=self.unit)
        except ModbusError as er:
            self.log.error(f"Cannot power on compressor: {er.code}")

    async def begin_disable(self, data):
        """Power off compressor before switching to disable state."""
        try:
            self.model.power_off(self.unit)
        except ModbusError as er:
            self.log.error(f"Cannot power off compressor: {er.code}")

    async def do_reset(self, data):
        """Reset compressor faults."""
        self.model.reset(self.unit)

    async def update_status(self):
        """Read compressor status - 3 status registers starting from address 0x30."""
        status = self.model.get_status(unit=self.unit)

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

        self._ready_to_start = status[0] & 0x01 == 0x01
        self._operating = status[0] & 0x02 == 0x02

        if self._operating:
            # None can be passed, as begin_enable and begin_disable called from
            # _do_change_state don't care about its content
            if self.summary_state != salobj.State.ENABLED:
                await self.do_enable(None)
        else:
            if self.summary_state != salobj.State.DISABLED:
                await self.do_disable(None)

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

        except Exception as ex:
            await self.fault(1, f"Error: {str(ex)}")


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
