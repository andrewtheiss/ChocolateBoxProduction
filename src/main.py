import asyncio
import logging.config
import yaml
from src.state_machine import PipelineCoordinator
from src.comms import init_serials
from src.ui import start_ui
from src.stations.base import load_stations

# Load configs
with open('config/stations.yaml') as f:
    stations_config = yaml.safe_load(f)
with open('config/states.yaml') as f:
    fsm_config = yaml.safe_load(f)

# Setup logging
logging.config.fileConfig('config/logging.ini')

async def main():
    serials = init_serials(stations_config)
    stations = load_stations(stations_config, fsm_config)  # Now loads with FSMs
    coordinator = PipelineCoordinator(serials, stations, fsm_config)
    start_ui(coordinator, stations)  # UI thread (non-async for rich)

    logging.info("Starting async production line...")
    await coordinator.run()  # Async loop for coordination

asyncio.run(main())