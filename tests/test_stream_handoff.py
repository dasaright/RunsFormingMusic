import unittest
from unittest.mock import AsyncMock
from relay_agent.main import RelayAgent

class StreamHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_stream_releases_slot_before_error_ack(self):
        agent=RelayAgent({})
        agent.stream_task=object();agent.stream_request_id='request'
        seen=[]
        async def send(payload):
            seen.append((payload,agent.stream_task,agent.stream_request_id))
        agent.send_json=AsyncMock(side_effect=send)
        await agent.stream('request','local:missing')
        self.assertEqual(seen[0][0]['type'],'stream_error')
        # Local overlays do not free the independent music slot.
        self.assertIsNotNone(seen[0][1])
        seen.clear()
        from unittest.mock import patch
        with patch('relay_agent.main.asyncio.create_subprocess_exec',new=AsyncMock(side_effect=OSError('decoder failed'))):
            await agent.stream('request','https://youtube.com/watch?v=abc')
        self.assertIsNone(seen[0][1]);self.assertIsNone(seen[0][2])
