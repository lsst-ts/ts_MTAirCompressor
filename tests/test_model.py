import asyncio
import logging
import unittest

from pymodbus.client.sync import ModbusTcpClient as ModbusClient

from lsst.ts import MTAirCompressor


class MTAirCompressorModelTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.log = logging.getLogger()
        self.log.addHandler(logging.StreamHandler())
        self.log.setLevel(logging.INFO)

        self.simulator = MTAirCompressor.simulator.create_server()
        hostname, port = self.simulator.server_address

        self.client = ModbusClient(hostname, port)
        assert self.client is not None

        self.simulator_task = asyncio.get_running_loop().run_in_executor(
            None, self.simulator.serve_forever
        )

    async def asyncTearDown(self):
        assert self.client is not None
        assert self.simulator is not None
        self.simulator.shutdown()
        self.simulator_task.cancel()

    async def test_get_status(self):
        model = MTAirCompressor.MTAirCompressorModel(self.client, 1)
        assert model.get_status() == [0x01, 0x00, 0x01]

    async def test_analog_data(self):
        model = MTAirCompressor.MTAirCompressorModel(self.client, 1)
        analog_data = model.get_analog_data()
        assert analog_data[0:10] == [2, 6, 7, 8, 9, 10, 11, 12, 13, 14]
