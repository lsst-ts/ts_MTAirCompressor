import asyncio
import logging
import unittest

from lsst.ts import MTAirCompressor


class MTAirCompressorModelTestCase(unittest.IsolatedAsyncioTestCase):
    def __init__(self):
        self.simulator = None

    @classmethod
    def setUpClass(cls):
        cls.log = logging.getLogger()
        cls.log.addHandler(logging.StreamHandler())
        cls.log.setLevel(logging.INFO)

    async def asyncSetUp(self):
        self.simulator = asyncio.create_task(MTAirCompressor.simulator.create_server())

    async def asyncTearDown(self):
        if self.simulator:
            await asyncio.wait_for(self.simulator.close(), 5)

    async def test_get_status(self):
        self.model = MTAirCompressor.MTAirCompressorModel("localhost", 5020)
        assert self.model.connect() is True
        assert self.model.get_status(1)[0] == 0xA000
