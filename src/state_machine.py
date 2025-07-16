import asyncio
from transitions.extensions.asyncio import AsyncMachine
import logging

class PipelineCoordinator:
    def __init__(self, serials, stations, fsm_config):
        self.serials = serials
        self.stations = stations  # Dict of station instances with their FSMs
        self.machine = AsyncMachine(model=self, states=fsm_config['global_states'], initial='RUNNING')

        # Define station order for pipelining
        self.station_order = ['dispenser', 'roller', 'taper']  # Add more as needed

    async def run(self):
        while True:
            await self.coordinate_pipeline()
            await asyncio.sleep(0.1)  # Throttle loop

    async def coordinate_pipeline(self):
        for i in range(len(self.station_order) - 1):
            current = self.stations[self.station_order[i]]
            next_station = self.stations[self.station_order[i+1]]

            if current.state == 'READY' and next_station.state == 'READY':
                # Previous can send to next
                await current.trigger('start_process')  # Triggers station's FSM
                logging.info(f"Advancing item from {self.station_order[i]} to {self.station_order[i+1]}")

        # Last station: When READY, cycle complete (e.g., alert or log)
        last = self.stations[self.station_order[-1]]
        if last.state == 'READY':
            logging.info("Item completed pipeline!")

        # Error check: If any ERROR, pause global
        if any(s.state == 'ERROR' for s in self.stations.values()):
            await self.to_ERROR()
            # Trigger reset or alert

    # Global callbacks, e.g.,
    async def on_enter_ERROR(self):
        logging.error("Global error! Pausing...")
        # Stop all stations, alert