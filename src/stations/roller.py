import asyncio
from .base import BaseStation
from src.comms import send_command


class Roller(BaseStation):
    async def on_process(self):
        serial = ...
        resp = await asyncio.to_thread(send_command, serial, 'start_roll')
        if resp == 'done':
            await self.process_success()
        else:
            await self.error()
