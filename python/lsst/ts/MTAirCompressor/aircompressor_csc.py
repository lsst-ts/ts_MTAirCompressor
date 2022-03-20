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

    async def do_enable(self, data):
        await super().do_enable(data)
        poweredOn = await self.client.write_register(0x12B, 0xFF01)
        if poweredOn.isError():
            raise RuntimeError(
                f"Cannot power on compressor: 0x{poweredOn.function_code:02X}"
            )

    async def end_disable(self, data):
        await self.client.write_register(0x12B, 0xFF00)
        self.telemetry_task.cancel()

    async def do_reset(self, data):
        await self.client.write_register(0x12D, 0xFF01)

    async def update_status(self):
        status = self.client.read_holding_registers(0x30, 3, unit=self.unit)
        if status.isError():
            raise RuntimeError(f"Cannot read status: 0x{status.function_code:02X}")

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
            raise RuntimeError(
                f"Cannot read compressor version: 0x{info1.function_code:02X}"
            )
        await self.evt_compressorInfo.set_write(
            softwareVersion=to_string(info1.registers[0:14]),
            serialNumber=to_string(info1.registers[14:23]),
        )

    async def update_analog_data(self):
        analog1 = self.client.read_holding_registers(0x1E, 1, unit=self.unit)
        analog2 = self.client.read_holding_registers(0x22, 14, unit=self.unit)
        if analog1.isError() or analog2.isError():
            raise RuntimeError(
                f"Cannot read telemetry: 0x{analog1.function_code:02X} 0x{analog2.function_code:02X}"
            )

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
            raise RuntimeError(f"Cannot read timers: 0x{timer.function_code:02X}")

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

            except Exception as er:
                print("Exception", str(er))
                traceback.print_exc()
                self.first_run = True

            await asyncio.sleep(1)
