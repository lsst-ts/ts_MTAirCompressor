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

__all__ = ["ErrorCode"]

import enum


class ErrorCode(enum.IntEnum):
    """Internal CsC error codes, reported with faults. Modbus faults can be
    reported as well, with their number the fault number. Unfortunately none of
    Delcos manuals we read contains the codes, and the ones we know doesn't
    match any generic Modbus codes. So far those are known:

    16 (0x10) - cannot start/stop compressor, as remote startup isn't allowed

    The other codes are:

    COULD_NOT_CONNECT - raised when ModBus TCP gateway cannot be contacted
    MODBUS_ERROR - generic Modbus error. Raised when Modbus response wasn't received
    """

    COULD_NOT_CONNECT = 98  # cannot connect to compressor
    MODBUS_ERROR = 99
