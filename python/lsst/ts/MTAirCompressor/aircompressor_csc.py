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

from pymodbus.client.asynchronous import schedulers

# from pymodbus.client.asynchronous.tcp import AsyncModbusTCPClient as ModbusClient
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
        # loop, self.client = ModbusClient(schedulers.ASYNC_IO, host=self.hostname, loop=asyncio.get_running_loop())
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

    async def get_info(self):
        def to_string(arr):
            return "".join(map(chr, arr))

        info1 = self.client.read_holding_registers(0xC7, 23, unit=self.unit)
        await self.evt_compressorInfo.set_write(
            force_output=True,
            softwareVersion=to_string(info1.registers[0:14]),
            serialNumber=to_string(info1.registers[14:23]),
        )

    async def telemetry_loop(self):
        while True:
            try:
                if self.first_run:
                    await self.get_info()
                    self.first_run = False

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
                    compressorPowerConsumption=analog2.registers[7],
                    compressorVolumePercentage=analog2.registers[8],
                    compressorVolume=analog2.registers[9],
                    groupVolume=analog2.registers[10],
                    stage1OutputPressure=analog2.registers[11],
                    linePressure=analog2.registers[12],
                    stage1OutputTemperature=analog2.registers[13],
                )

            except Exception as er:
                print("Exception", str(er))
                traceback.print_exc()
                self.first_run = True

            await asyncio.sleep(1)
