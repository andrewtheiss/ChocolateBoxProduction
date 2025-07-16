from transitions.extensions.asyncio import AsyncMachine

class BaseStation:
    def __init__(self, config, fsm_config):
        self.config = config
        self.machine = AsyncMachine(model=self, states=['IDLE', 'PROCESSING', 'READY', 'ERROR'], initial='READY')
        
        # Load shared transitions
        for trans in fsm_config['station_transitions']:
            self.machine.add_transition(**trans)
        
        # Callbacks
        self.machine.on_enter_PROCESSING(self.on_process)
        self.machine.on_enter_READY(self.on_ready)
        self.machine.on_enter_ERROR(self.on_error)

    async def on_process(self):
        raise NotImplementedError("Implement station-specific processing")

    async def on_ready(self):
        logging.info(f"{self.__class__.__name__} is READY for next item")

    async def on_error(self):
        logging.error(f"{self.__class__.__name__} in ERROR")

# Load function updated
def load_stations(config, fsm_config):
    from .dispenser import Dispenser
    from .roller import Roller
    from .taper import Taper
    return {
        'dispenser': Dispenser(config['dispenser'], fsm_config),
        'roller': Roller(config['roller'], fsm_config),
        'taper': Taper(config['taper'], fsm_config),
    }