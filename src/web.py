import logging
import logging.config
import yaml

with open('config/stations.yaml') as f:
    stations_config = yaml.safe_load(f)
with open('config/states.yaml') as f:
    fsm_config = yaml.safe_load(f)

logging.config.fileConfig('config/logging.ini')

from src.web_ui import start_web_ui
start_web_ui(stations_config, fsm_config)
