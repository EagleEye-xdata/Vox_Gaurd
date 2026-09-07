import unittest
import asyncio
import struct
import uuid
import os
import threading
from pathlib import Path

# Need to mock the bridge to avoid hitting the real gateway
from unittest.mock import MagicMock, patch

from .server import AudioSocketServer
from .config import config

class TestAudioSocketServer(unittest.IsolatedAsyncioTestCase):
    async def test_audio_socket_flow(self):
        # We will mock the reader and writer
        reader = asyncio.StreamReader()
        writer = MagicMock(spec=asyncio.StreamWriter)
        writer.get_extra_info.return_value = "127.0.0.1:12345"

        # Prepare mock AudioSocket packets
        call_uuid = uuid.uuid4()
        
        # 1. ID packet (0x01)
        id_payload = call_uuid.bytes
        id_packet = struct.pack('>B', 0x01) + struct.pack('>H', 16) + id_payload
        
        # 2. Audio packet (0x10)
        audio_payload = b'\x00\x00' * 16000 # 1 second of silence
        audio_packet = struct.pack('>B', 0x10) + struct.pack('>H', len(audio_payload)) + audio_payload
        
        # 3. Hangup packet (0x00)
        hangup_packet = struct.pack('>B', 0x00) + struct.pack('>H', 0)
        
        # Feed the mock reader
        reader.feed_data(id_packet + audio_packet + hangup_packet)
        reader.feed_eof()

        server = AudioSocketServer()
        server.bridge.start_session = MagicMock(return_value="mock_call_id")
        server.bridge.stop_session = MagicMock()
        
        # We need a dummy reader to prevent the FIFO from blocking
        stop_reader = threading.Event()
        def dummy_reader():
            fifo_path = server.audio_dir / f"live-{call_uuid}.wav"
            # Wait for FIFO to be created
            while not fifo_path.exists() and not stop_reader.is_set():
                import time
                time.sleep(0.01)
            if stop_reader.is_set(): return
            
            try:
                with open(fifo_path, 'rb') as f:
                    while not stop_reader.is_set():
                        data = f.read(1024)
                        if not data:
                            break
            except Exception:
                pass
                
        reader_thread = threading.Thread(target=dummy_reader)
        reader_thread.start()

        try:
            # Run the client handler
            await server.handle_client(reader, writer)
            
            # Verify the bridge was called
            await asyncio.sleep(0.1)
            
            server.bridge.start_session.assert_called_once()
            server.bridge.stop_session.assert_called_once_with("mock_call_id")
            
            # Verify FIFO was cleaned up
            fifo_path = server.audio_dir / f"live-{call_uuid}.wav"
            self.assertFalse(fifo_path.exists())
        finally:
            stop_reader.set()
            reader_thread.join()

if __name__ == '__main__':
    unittest.main()
