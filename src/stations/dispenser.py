from .base import BaseStation
from src.comms import send_command

class Dispenser(BaseStation):
    async def on_process(self):
        serial = ...  # Passed or global; assume from coordinator
        resp = await asyncio.to_thread(send_command, serial, 'start_dispense')  # Thread for blocking IO
        if resp == 'done':
            await self.process_success()
        else:
            await self.error()