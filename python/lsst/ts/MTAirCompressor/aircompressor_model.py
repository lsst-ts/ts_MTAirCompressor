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


class Register(enum.IntEnum):
    """Address of registers of interest.

    Please see Delcos XL documentation for details.
    https://confluence.lsstcorp.org/display/LTS/Datasheets (you need LSST login)
    """

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

    RUNNING_HOURS = 0x39  # 64bit, 2 registers
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
    """Exception raised on modbus errors. Please note that shall be superseded by
    pymodbus solution, if it ever materialize, See:
    https://github.com/riptideio/pymodbus/issues/298

    Parameters
    ----------
    modbusException : `pymodbus.exception.ModbusException`
        ModBus orignal exception
    message : `str`
        Message associated with the error
    """

    def __init__(self, modbusException, message=""):
        super().__init__(message)
        self.exception = modbusException

    def __str__(self):
        if self.args[0] is not None and self.args[0] != "":
            return f"{str(self.exception)} - {self.args[0]}"
        return str(self.exception)


class MTAirCompressorModel:
    """Model for compressor.

    Handles compressor communication. Throws ModbusError on errors, overcoming
    PyModbus deficiency to do so.

    Parameters
    ----------
    connection : `ModbusClient`
        Connection to compresor controller.
    unit : `int`
        Compressor unit (address on modbus).
    """

    def __init__(self, connection, unit):
        self.connection = connection
        self.unit = unit

    def set_register(self, address, value, error_status):
        """Set ModBus register value.

        Parameters
        ----------
        address : `int(0xffff)`
            Address of register to be set.
        value : `int(0xffff)`
            New register value (16-bit integer).

        Returns
        -------
        response : `class`
            PyModbus response to call.

        Raises
        ------
        ModbusError
            When register cannot be set.
        """
        response = self.connection.write_registers(address, [value], unit=self.unit)
        if response.isError():
            raise ModbusError(response, error_status)
        return response

    def reset(self):
        """Reset compressor errors.

        Returns
        -------
        response : `class`
            PyModbus response to call to set reset register.

        Raises
        ------
        ModbusError
            When reset cannot be performed.
        """
        return self.set_register(Register.RESET, 0xFF01, "Cannot reset compressor")

    def power_on(self):
        """Power on compressor.

        Returns
        -------
        response : `class`
            PyModbus response to call to power on compressor.

        Raises
        ------
        ModbusError
            When compressor cannot be powered on. That includes power not
            configured to operate remotely - original_code in return then
            equals 16.
        """
        return self.set_register(
            Register.REMOTE_CMD, 0xFF01, "Cannot power on compressor"
        )

    def power_off(self):
        """Power off compressor.

        Returns
        -------
        response : `class`
            PyModbus response to call to power off compressor.

        Raises
        ------
        ModbusError
            When compressor cannot be powered off. That includes power not
            configured to operate remotely - original_code in return then
            equals 16.
        """
        return self.set_register(
            Register.REMOTE_CMD, 0xFF00, "Cannot power down compressor"
        )

    def get_registers(self, address, count, error_status):
        """
        Returns registers.

        Parameters
        ----------
        address : `int`
            Register address.
        count : `int`
            Number of registers to read.
        error_status : `str`
            Error status to fill in ModbusError raised on error.

        Raises
        ------
        ModbusError
            When register(s) cannot be retrieved.
        """
        status = self.connection.read_holding_registers(address, count, unit=self.unit)
        if status.isError():
            raise ModbusError(status, error_status)
        return status.registers

    def get_status(self):
        """Read compressor status - 3 status registers starting from address 0x30.

        Raises
        ------
        ModbusError
            When registers cannot be retrieved.
        """
        return self.get_registers(Register.STATUS, 3, "Cannot read status")

    def get_error_registers(self):
        """Read compressor errors - 16 registers starting from address 0x63.

        Those are E4xx and A6xx registers, all bit masked. Please see Delcos
        manual for details.

        Raises
        ------
        ModbusError
            When registers cannot be retrieved.
        """
        return self.get_registers(
            Register.ERROR_E400, 16, "Cannot read error registers"
        )

    def get_compressor_info(self):
        """Read compressor info - 23 registers starting from address 0x63.

        Includes software version and serial number.

        Raises
        ------
        ModbusError
            When registers cannot be retrieved.
        """
        return self.get_registers(
            Register.SOFTWARE_VERSION, 23, "Cannot read compressor info"
        )

    def get_analog_data(self):
        """Read compressor info - register 0x1E and 14 registers starting from address 0x22.

        Those form compressor telemetry - includes various measurements. See
        Register and Delcos manual for indices.

        Raises
        ------
        ModbusError
            When registers cannot be retrieved.
        """
        return self.get_registers(
            Register.WATER_LEVEL, 1, "Cannot read water level"
        ) + self.get_registers(Register.TARGET_SPEED, 14, "Cannot read analog data")

    def get_timers(self):
        """Read compressor timers - 8 registers starting from address 0x39.

        Those form compressor running hours etc.

        Raises
        ------
        ModbusError
            When registers cannot be retrieved.
        """
        return self.get_registers(Register.RUNNING_HOURS, 8, "Cannot read timers")
