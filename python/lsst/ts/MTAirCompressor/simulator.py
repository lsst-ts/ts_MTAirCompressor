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

__all__ = ["create_server"]

from pymodbus.server.sync import ModbusTcpServer
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusSlaveContext,
    ModbusServerContext,
)

from .aircompressor_model import Register


class SimulatedHrBlock(ModbusSequentialDataBlock):
    def __init__(self):
        block = [0] * 0x1E + list(range(1, 20)) + [0x01, 0x0, 0x01] + [0] * 0x120
        super().__init__(0, block)

    def setValues(self, address, values):
        # there is mismatch in indexing, + 1 is needed on Register.xxx side
        if address == Register.REMOTE_CMD + 1:
            super().setValues(
                Register.STATUS + 1, [0x02] if values[0] == 0xFF01 else [0x01]
            )
            super().setValues(
                Register.INHIBIT + 1, [0x00] if values[0] == 0xFF01 else [0x01]
            )
        super().setValues(address, values)


def create_server():
    """Create simulator server. Uses arbitrary constants for values, please
    consult Delcos XL register map - see Register enum.

    Returns
    -------
    server : `ModbusTcpServer`
        Created server instance.
    """
    store = ModbusSlaveContext(hr=SimulatedHrBlock())
    context = ModbusServerContext(slaves=store, single=True)

    return ModbusTcpServer(context, address=("localhost", 0))
