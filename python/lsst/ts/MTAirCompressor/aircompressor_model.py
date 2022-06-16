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

__all__ = ["ModbusError", "MTAirCompressorModel"]

import enum

# although pymodbus supports asyncio, it's uselless to use asyncio version
# as there isn't any extra processing which can occur while waiting for modbus
# data
from pymodbus.client.sync import ModbusTcpClient as ModbusClient

from .enums import ErrorCode


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


class ModbusError(RuntimeError):
    """Exception raised on modbus errors. Please note that shall be superset by
    pymodbus solution, if it ever materialize, See:
    https://github.com/riptideio/pymodbus/issues/298

    Parameters
    ----------
    code : `int`
        ModBus error code
    message : `str`
        Message associated with the error
    """

    def __init__(self, code, message):
        self.code = code
        self.message = message


class MTAirCompressorModel(ModbusClient):
    """Model for compressor. Handles compressor communication. Throws
    ModbusError on errors, overcoming PyModbus deficiency to do so. It doesn't
    manage Modbus addresses - address/unit parameter needs to be added to all
    calls, similarly to ModbusClient.

    Parameters
    ----------
    hostname : `str`
        ModBus hostname.
    port : `int`, optional
        ModBus port.
    """

    def __init__(self, hostname, port=502):
        super().__init__(host=hostname, port=port)

    def connect(self):
        ret = super().connect()
        if ret is False:
            raise ModbusError(
                ErrorCode.COULD_NOT_CONNECT,
                "Cannot establish connectiont to {self.host}:{self.port}",
            )
        return ret

    def set_register(self, address, value, unit, error_status):
        response = self.write_register(address, value, unit=unit)
        if response.isError():
            raise ModbusError(response.original_code, error_status)
        return response

    def reset(self, unit):
        return self.set_register(
            Registers.RESET, 0xFF01, unit, "Cannot reset compressor"
        )

    def power_on(self, unit):
        return self.set_register(
            Registers.REMOTE_CMD, 0xFF01, unit, "Cannot power on compressor"
        )

    def power_off(self, unit):
        return self.set_register(
            Registers.REMOTE_CMD, 0xFF00, unit, "Cannot power down compressor"
        )

    def get_registers(self, address, count, unit, error_status):
        status = self.read_holding_registers(address, count, unit=unit)
        if status.isError():
            raise ModbusError(status.original_code, error_status)
        return status.registers

    def get_status(self, unit):
        """Read compressor status - 3 status registers starting from address 0x30."""
        return self.get_registers(Registers.STATUS, 3, unit, "Cannot read status")

    def get_error_registers(self, unit):
        return self.get_registers(
            Registers.ERROR_E400, 16, unit, "Cannot read error registers"
        )

    def get_compressor_info(self, unit):
        return self.get_registers(
            Registers.SOFTWARE_VERSION, 23, unit, "Cannot read compressor info"
        )

    def get_analog_data(self, unit):
        return self.get_registers(
            Registers.WATER_LEVEL, 1, unit, "Cannot read water level"
        ) + self.get_registers(
            Registers.TARGET_SPEED, 14, unit, "Cannot read analog data"
        )

    def get_timers(self, unit):
        return self.get_registers(Registers.RUNNIG_HOURS, 8, unit, "Cannot read timers")
