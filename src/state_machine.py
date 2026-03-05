import asyncio
from transitions.extensions.asyncio import AsyncMachine
import logging


class PipelineCoordinator:
    def __init__(self, serials, stations, fsm_config):
        self.serials = serials
        self.stations = stations
        self.machine = AsyncMachine(
            model=self,
            states=fsm_config['global_states'],
            initial='RUNNING',
        )

        # Only coordinate stations that have a serial connection
        all_order = ['dispenser', 'roller', 'taper']
        self.station_order = [s for s in all_order if serials.get(s) is not None]
        logging.info(f"Active pipeline: {' → '.join(self.station_order) if self.station_order else '(none)'}")

    async def run(self):
        while True:
            if self.state == 'RUNNING':
                await self.coordinate_pipeline()
            await asyncio.sleep(1)

    async def coordinate_pipeline(self):
        if len(self.station_order) < 2:
            return

        for i in range(len(self.station_order) - 1):
            current = self.stations[self.station_order[i]]
            next_station = self.stations[self.station_order[i + 1]]

            if current.state == 'READY' and next_station.state == 'READY':
                await current.trigger('start_process')
                logging.info(f"Advancing: {self.station_order[i]} → {self.station_order[i + 1]}")

        last = self.stations[self.station_order[-1]]
        if last.state == 'READY':
            logging.info("Item completed pipeline!")

        active_stations = [self.stations[n] for n in self.station_order]
        if any(s.state == 'ERROR' for s in active_stations):
            if self.state != 'ERROR':
                await self.to_ERROR()

    async def on_enter_ERROR(self):
        logging.error("Global error — coordinator paused. Check stations.")

    async def on_enter_RUNNING(self):
        logging.info("Coordinator running.")
