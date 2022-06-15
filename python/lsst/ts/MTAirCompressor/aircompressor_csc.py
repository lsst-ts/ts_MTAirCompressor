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
import enum
from lsst.ts import salobj, utils
from lsst.ts.salobj import base

# although pymodbus supports asyncio, it's uselless to use asyncio version
# as there isn't any extra processing which can occur while waiting for modbus
# data
from pymodbus.client.sync import ModbusTcpClient as ModbusClient

from . import __version__
from . import enums
from .simulator import create_server


# Address of registers of interest. Please see Delcos XL documentation for details
class Registers(enum.IntEnum):
    # telemetry block
    WATER_LEVEL = 0x1E

    TARGET_SPEED = 0x22
    MOTOR_CURRENT = 0x23
    HEATSINK_TEMP = 0x24
    DCLINK_VOLTAGE = 0x25
    MOTOR_SPEED_PERCENTAGE = 0x26
    MOTOR_SPEED_RPM = 0x27
    MOTOR_INPUT = 0x28
    COMPRESSOR_POWER_CONSUMATION = 0x29
    COMPRESSOR_VOLUME_PERCENTAGE = 0x2A
    COMPRESSOR_VOLUME = 0x2B
    GROUP_VOLUME = 0x2C
    STAGE_1_OUTPUT_PRESSURE = 0x2D
    LINE_PRESSURE = 0x2E
    STAGE_1_OUTPUT_TEMPERATURE = 0x2F

    RUNNIG_HOURS = 0x39  # 64bit, 2 registers
    LOADED_HOURS = 0x3B  # 64 bit, 2 registers
    LOWEST_SERVICE_COUNTER = 0x3C
    RUN_ON_TIMER = 0x3D
    LOADED_HOURS_50_PERECENT = 0x3E  # 64 bit, 2 registers

    STATUS = 0x30  # flags - started, ..
    ERROR_E400 = 0x63  # 16 registers with error and warning flags

    SOFTWARE_VERSION = 0xC7  # 14 ASCII registers
    SERIAL_NUMER = 0xD5  # 9 ASCII registers

    REMOTE_CMD = 0x12B  # power on/off, if remote commanding is enabled
    RESET = 0x12D  # reset errors & warnings


class ModbusError(base.ExpectedError):
    """Exception raised on modbus errors. Please note that shall be superset by
    pymodbus solution, if it ever materialize, See:
    https://github.com/riptideio/pymodbus/issues/298

    Parameters
    ----------
    modbus_exception : `pymodbus.pdu.ExceptionResponse`
        Exception returned from Modbus function
    """

    def __init__(self, what, modbus_exception, address):
        if modbus_exception.original_code == 4:
            message = (
                f"Cannot address 0x{address:04x}: {modbus_exception.exception_code}"
            )
        elif modbus_exception.original_code == 6:
            message = f"Cannot write register address 0x{address:04x}: {modbus_exception.exception_code}"
        else:
            message = (
                f"Cannot call function {modbus_exception.function_code} : "
                f"{modbus_exception.exception_code}, address 0x{address:04x}"
            )
        super().__init__(what + " " + message)

        self.exception = modbus_exception


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
        self.client = None
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

    async def _connect(self):
        self.client = ModbusClient(host=self.hostname, port=self.port)

        if self.client.connect() is False:
            self.fault(
                enums.CscErrorCode.COULD_NOT_CONNECT,
                "Cannot establish connection to {self.hostname}:{self.port}",
            )
            return

        self.telemetry_task = asyncio.create_task(self.telemetry_loop())

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

        await self._connect()

    async def end_enable(self, data):
        """Power on compressor after switching to enable state. Raise exception
        if compressor cannot be powered on."""
        if self._ready_to_start is False:
            return

        poweredOn = self.client.write_register(
            Registers.REMOTE_CMD, 0xFF01, unit=self.unit
        )
        if poweredOn.isError():
            await self.fault(poweredOn.original_code, "Cannot power on compressor")

    async def begin_disable(self, data):
        """Power off compressor before switching to disable state."""
        poweredDown = self.client.write_register(
            Registers.REMOTE_CMD, 0xFF00, unit=self.unit
        )
        if poweredDown.isError():
            raise ModbusError(
                "Cannot power off compressor", poweredDown, Registers.REMOTE_CMD
            )

    async def do_reset(self, data):
        """Reset compressor faults."""
        if self.client is None:
            return
        reseted = self.client.write_register(Registers.RESET, 0xFF01, unit=self.unit)
        if reseted.isError():
            await self.fault(reseted.original_code, "Cannot reset compressor")

    async def update_status(self):
        """Read compressor status - 3 status registers starting from address 0x30."""
        status = self.client.read_holding_registers(Registers.STATUS, 3, unit=self.unit)
        if status.isError():
            raise ModbusError("Cannot read status", status, Registers.STATUS)

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
                status.registers[0],
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
                status.registers[2],
            ),
        )

        self._ready_to_start = status.registers[0] & 0x01 == 0x01
        self._operating = status.registers[0] & 0x02 == 0x02

        if self._operating:
            # None can be passed, as begin_enable and begin_disable called from _do_change_state don't care about its content
            if self.summary_state != salobj.State.ENABLED:
                await self.do_enable(None)
        else:
            if self.summary_state != salobj.State.DISABLED:
                await self.do_disable(None)

    async def update_errorsWarnings(self):
        """Read compressor errors and warnings - 16 registers starting from address 0x63."""
        errorsWarnings = self.client.read_holding_registers(
            Registers.ERROR_E400, 16, unit=self.unit
        )
        if errorsWarnings.isError():
            raise ModbusError(
                "Cannot read errors and warnings", errorsWarnings, Registers.ERROR_E400
            )

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
                errorsWarnings.registers[0],
            ),
            **_statusBits(
                ["heavyStartupE416"],
                errorsWarnings.registers[1],
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
                errorsWarnings.registers[6],
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
                errorsWarnings.registers[8],
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
                errorsWarnings.registers[9],
            ),
            **_statusBits(
                ["temperatureHighVSDA700"],
                errorsWarnings.registers[14],
            ),
        )

    async def update_compressor_info(self):
        """Read compressor info - serial number and software version."""

        def to_string(arr):
            return "".join(map(chr, arr))

        info1 = self.client.read_holding_registers(
            Registers.SOFTWARE_VERSION, 23, unit=self.unit
        )
        if info1.isError():
            raise ModbusError(
                "Cannot read compressor version", info1, Registers.SOFTWARE_VERSION
            )
        await self.evt_compressorInfo.set_write(
            softwareVersion=to_string(info1.registers[0:14]),
            serialNumber=to_string(info1.registers[14:23]),
        )

    async def update_analog_data(self):
        """Read compressor analog (telemetry-worth) data."""
        water_level_reg = self.client.read_holding_registers(
            Registers.WATER_LEVEL, 1, unit=self.unit
        )
        if water_level_reg.isError():
            raise ModbusError(
                "Cannot read water level telemetry",
                water_level_reg,
                Registers.WATER_LEVEL,
            )
        analog2 = self.client.read_holding_registers(
            Registers.TARGET_SPEED, 14, unit=self.unit
        )
        if analog2.isError():
            raise ModbusError("Cannot read telemetry", analog2, Registers.TARGET_SPEED)

        await self.tel_analogData.set_write(
            force_output=True,
            waterLevel=water_level_reg.registers[0],
            targetSpeed=analog2.registers[0],
            motorCurrent=analog2.registers[1] / 10.0,
            heatsinkTemperature=analog2.registers[2],
            dclinkVoltage=analog2.registers[3],
            motorSpeedPercentage=analog2.registers[4],
            motorSpeedRPM=analog2.registers[5],
            motorInput=analog2.registers[6] / 10.0,
            compressorPowerConsumption=analog2.registers[7] / 10.0,
            compressorVolumePercentage=analog2.registers[8],
            compressorVolume=analog2.registers[9] / 10.0,
            groupVolume=analog2.registers[10] / 10.0,
            stage1OutputPressure=analog2.registers[11],
            linePressure=analog2.registers[12],
            stage1OutputTemperature=analog2.registers[13],
        )

    async def update_timer(self):
        """Read compressors timers."""
        timer = self.client.read_holding_registers(
            Registers.RUNNIG_HOURS, 8, unit=self.unit
        )
        if timer.isError():
            raise ModbusError("Cannot read timers", timer, Registers.RUNNIG_HOURS)

        def to_64(a):
            return a[0] << 16 | a[1]

        await self.evt_timerInfo.set_write(
            runningHours=to_64(timer.registers[0:2]),
            loadedHours=to_64(timer.registers[2:4]),
            lowestServiceCounter=timer.registers[4],
            runOnTimer=timer.registers[5],
            loadedHours50Percent=to_64(timer.registers[6:8]),
        )

    async def telemetry_loop(self):
        """Runs telemetry loop."""
        timerUpdate = 0
        while True:
            try:
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
            except ModbusError as me:
                await self.fault(1, str(me))
                self.first_run = True

            except Exception as er:
                await self.fault(1, str(er))
                self.first_run = True

            await asyncio.sleep(1)


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
