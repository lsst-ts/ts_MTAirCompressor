# This file is part of ts_AirCompressors.
#
# Developed for the LSST Data Management System.
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

import asyncio
import traceback
from lsst.ts import salobj, utils

# from pymodbus.client.asynchronous import schedulers
# from pymodbus.client.asynchronous.tcp import (
#   AsyncModbusTCPClient as ModbusClient
# )
from pymodbus.client.sync import ModbusTcpClient as ModbusClient

from . import __version__

class ModbusError(RuntimeError):
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
            message = f"Cannot address 0x{address:04x}: {modbus_exception.exception_code}"
        elif modbus_exception.original_code == 6:
            message = f"Cannot write register address 0x{address:04x}: {modbus_exception.exception_code}"
        else:
            message = f"Cannot call function {modbus_exception.function_code} : {modbus_exception.exception_code}, address 0x{address:04x}"
        super().__init__(what + " " + message)

        self.exception = modbus_exception

class MTAirCompressorCsc(salobj.BaseCsc):
    """AirCompressors CSC

    Parameters
    ----------
    index : `int`
        CSC index.
    """

    version = __version__

    def __init__(self, index):
        super().__init__(name="MTAirCompressor", index=index)

        self.unit = 1
        self.hostname = "m1m3cam-aircomp01.cp.lsst.org"

        self.first_run = True
        self.client = None
        self.telemetry_task = utils.make_done_future()

    async def do_start(self, data):
        await super().do_start(data)
        # loop, self.client = ModbusClient(
        #    schedulers.ASYNC_IO,
        #    host=self.hostname,
        #    loop=asyncio.get_running_loop(),
        # )
        self.client = ModbusClient(host=self.hostname)
        self.client.connect()
        self.telemetry_task = asyncio.create_task(self.telemetry_loop())

    async def end_enable(self, data):
        await super().do_enable(data)
        poweredOn = self.client.write_register(0x12B, 0xFF01, unit=self.unit)
        if poweredOn.isError():
            raise ModbusError("Cannot power on compressor", poweredOn, 0x12B)

    async def end_disable(self, data):
        poweredDown = self.client.write_register(0x12B, 0xFF00, unit=self.unit)
        if poweredDown.isError():
            raise ModbusError("Cannot power off compressor", poweredDown, 0x12B)
        self.telemetry_task.cancel()

    async def do_reset(self, data):
        reseted = self.client.write_register(0x12D, 0xFF01, unit=self.unit)
        if reseted.isError():
            self.fail("Cannot reset compressor")
            raise ModbusError("Cannot reset compressor", poweredDown, 0x12B)

    async def update_status(self):
        status = self.client.read_holding_registers(0x30, 3, unit=self.unit)
        if status.isError():
            raise ModbusError("Cannot read status", status, 0x30)

        def _statusBits(fields, value):
            ret = {}
            for f in fields:
                ret[f] = value & 0x0001
                value >>= 1
            return ret

        self.evt_status.set(
            **_statusBits(
                (
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
                ),
                status.registers[0],
            )
        )
        await self.evt_status.set_write(
            force_output=self.first_run,
            **_statusBits(
                (
                    "startByRemote",
                    "startWithTimerControl",
                    "startWithPressureRequirement",
                    "startAfterDePressurise",
                    "startAfterPowerLoss",
                    "startAfterDryerPreRun",
                ),
                status.registers[2],
            ),
        )

    async def update_compressor_info(self):
        def to_string(arr):
            return "".join(map(chr, arr))

        info1 = self.client.read_holding_registers(0xC7, 23, unit=self.unit)
        if info1.isError():
            raise ModbusError("Cannot read compressor version", info1, 0xC7)
        await self.evt_compressorInfo.set_write(
            softwareVersion=to_string(info1.registers[0:14]),
            serialNumber=to_string(info1.registers[14:23]),
        )

    async def update_analog_data(self):
        analog1 = self.client.read_holding_registers(0x1E, 1, unit=self.unit)
        if analog1.isError():
            raise ModbusError("Cannot read telemetry", analog1, 0x1E)
        analog2 = self.client.read_holding_registers(0x22, 14, unit=self.unit)
        if analog2.isError():
            raise ModbusError("Cannot read telemetry", analog2, 0x22)

        await self.tel_analogData.set_write(
            force_output=True,
            waterLevel=analog1.registers[0],
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
        timer = self.client.read_holding_registers(0x39, 8, unit=self.unit)
        if timer.isError():
            raise ModbusError("Cannot read timers", timer, 0x39)

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
        timerUpdate = 0
        while True:
            try:
                await self.update_status()
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
                self.fail(None, str(me))
                self.first_run = True

            except Exception as er:
                self.log.exception(er)
                self.first_run = True

            await asyncio.sleep(1)
