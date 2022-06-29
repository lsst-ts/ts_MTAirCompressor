import unittest

from lsst.ts import salobj, MTAirCompressor


class MTAirCompressorCscTestCase(
    unittest.IsolatedAsyncioTestCase, salobj.BaseCscTestCase
):
    def basic_make_csc(self, initial_state, config_dir, simulation_mode, index=1):
        return MTAirCompressor.MTAirCompressorCsc(
            index, initial_state=initial_state, simulation_mode=1
        )

    async def test_standard_state_transitions(self):
        async with self.make_csc(index=2):
            await self.check_standard_state_transitions(
                enabled_commands=[], skip_commands=["reset"]
            )

    async def test_bin_script(self):
        await self.check_bin_script(
            name="MTAirCompressor",
            exe_name="run_mtaircompressor",
            index=1,
        )
        await self.check_bin_script(
            name="MTAirCompressor",
            exe_name="run_mtaircompressor",
            index=2,
        )
