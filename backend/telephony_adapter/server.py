import asyncio
import logging
import os
import struct
import wave
import uuid
import threading
from pathlib import Path
from .config import config
from .bridge import IngestionBridge

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TelephonyAdapter")

class AudioSocketServer:
    def __init__(self):
        self.bridge = IngestionBridge()
        self.audio_dir = Path(config.AUDIO_DIR)
        self.audio_dir.mkdir(parents=True, exist_ok=True)

    def write_wav_header(self, fifo_path: str):
        """Write a dummy WAV header so soundfile doesn't complain."""
        # 44 bytes header for 8kHz 16-bit mono PCM
        # Size is set to a large number (e.g. 0x7FFFFFFF) to simulate a stream
        with open(fifo_path, 'wb') as f:
            f.write(b'RIFF')
            f.write(struct.pack('<I', 0x7FFFFFFF)) # ChunkSize
            f.write(b'WAVE')
            f.write(b'fmt ')
            f.write(struct.pack('<I', 16)) # Subchunk1Size
            f.write(struct.pack('<H', 1))  # AudioFormat (PCM)
            f.write(struct.pack('<H', 1))  # NumChannels
            f.write(struct.pack('<I', 8000)) # SampleRate
            f.write(struct.pack('<I', 8000 * 2)) # ByteRate
            f.write(struct.pack('<H', 2))  # BlockAlign
            f.write(struct.pack('<H', 16)) # BitsPerSample
            f.write(b'data')
            f.write(struct.pack('<I', 0x7FFFFFFF - 36)) # Subchunk2Size

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info('peername')
        logger.info(f"New PBX connection from {peer}")
        
        call_uuid = None
        fifo_path = None
        gateway_call_id = None
        fifo_fd = None

        try:
            while True:
                # Read Type (1 byte)
                type_byte = await reader.readexactly(1)
                payload_type = type_byte[0]

                # Read Length (2 bytes)
                len_bytes = await reader.readexactly(2)
                payload_length = struct.unpack('>H', len_bytes)[0]

                if payload_length > 0:
                    payload = await reader.readexactly(payload_length)
                else:
                    payload = b""

                if payload_type == 0x00:
                    # Hangup
                    logger.info("Call hung up by PBX.")
                    break
                    
                elif payload_type == 0x01:
                    # ID (UUID)
                    call_uuid = str(uuid.UUID(bytes=payload))
                    logger.info(f"Call UUID: {call_uuid}")
                    
                    # Create FIFO
                    fifo_filename = f"live-{call_uuid}.wav"
                    fifo_path = str(self.audio_dir / fifo_filename)
                    
                    if os.path.exists(fifo_path):
                        os.remove(fifo_path)
                    os.mkfifo(fifo_path)
                    
                    # Start session in background to not block FIFO open
                    def start_session():
                        try:
                            nonlocal gateway_call_id
                            gateway_call_id = self.bridge.start_session(fifo_filename, f"Asterisk-{call_uuid[:8]}")
                            logger.info(f"Started Gateway Session: {gateway_call_id}")
                        except Exception as e:
                            logger.error(f"Error starting session: {e}")

                    threading.Thread(target=start_session).start()

                    # Write header
                    self.write_wav_header(fifo_path)
                    
                    # Open FIFO for appending
                    fifo_fd = open(fifo_path, 'ab')
                    
                elif payload_type == 0x10:
                    # Audio data
                    if fifo_fd:
                        fifo_fd.write(payload)
                        fifo_fd.flush()

        except asyncio.IncompleteReadError:
            logger.info(f"Connection closed by PBX {peer}")
        except Exception as e:
            logger.error(f"Error handling connection: {e}")
        finally:
            if fifo_fd:
                fifo_fd.close()
            if gateway_call_id:
                self.bridge.stop_session(gateway_call_id)
            if fifo_path and os.path.exists(fifo_path):
                os.remove(fifo_path)
            writer.close()
            await writer.wait_closed()

    async def serve(self):
        server = await asyncio.start_server(
            self.handle_client, config.PBX_HOST, config.PBX_PORT)
        
        addr = server.sockets[0].getsockname()
        logger.info(f"Asterisk AudioSocket Server listening on {addr}")

        async with server:
            await server.serve_forever()

if __name__ == "__main__":
    server = AudioSocketServer()
    asyncio.run(server.serve())
