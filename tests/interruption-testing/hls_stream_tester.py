#!/usr/bin/env python3
"""
HLS Stream Testing Utility for Open Voice SDK

Tests interruption handling using live HLS audio streams.
Useful for integration testing with real-world audio sources.
"""

import asyncio
import subprocess
import tempfile
import os
import logging
from typing import Optional, Callable
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Live HLS test streams
HLS_TEST_STREAMS = {
    "radio_mast_mp3": "https://streams.radiomast.io/ref-128k-mp3-stereo/hls.m3u8",
    "radio_mast_aac": "https://streams.radiomast.io/ref-128k-aaclc-stereo/hls.m3u8",
    "akamai_live": "https://cph-p2p-msl.akamaized.net/hls/live/2000341/test/master.m3u8",
    "apple_fmp4": "https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_fmp4/master.m3u8",
    "unified_streaming": "https://demo.unified-streaming.com/k8s/features/stable/video/tears-of-steel/tears-of-steel.ism/.m3u8",
    "ireplay_blender": "https://ireplay.tv/test/blender.m3u8",
}


class HLSStreamTester:
    """Test interruption handling using HLS streams"""

    def __init__(self, runtime_url: str = "ws://localhost:8011"):
        self.runtime_url = runtime_url
        self.ffmpeg_process: Optional[subprocess.Popen] = None
        self.temp_dir = tempfile.mkdtemp()

    async def stream_hls_to_runtime(
        self, hls_url: str, duration: float = 30.0, chunk_duration: float = 0.1
    ) -> bool:
        """
        Stream HLS audio to Open Voice runtime

        Args:
            hls_url: HLS stream URL
            duration: How long to stream (seconds)
            chunk_duration: Audio chunk size (seconds)

        Returns:
            True if successful
        """
        output_file = os.path.join(self.temp_dir, "stream_audio.wav")

        # Download and convert HLS to WAV using ffmpeg
        cmd = [
            "ffmpeg",
            "-i",
            hls_url,
            "-t",
            str(duration),
            "-ar",
            "16000",  # Sample rate
            "-ac",
            "1",  # Mono
            "-acodec",
            "pcm_s16le",  # 16-bit PCM
            "-y",  # Overwrite output
            output_file,
        ]

        logger.info(f"Downloading HLS stream: {hls_url}")
        logger.info(f"Command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=duration + 30
            )

            if result.returncode != 0:
                logger.error(f"FFmpeg failed: {result.stderr}")
                return False

            if not os.path.exists(output_file):
                logger.error("Output file not created")
                return False

            file_size = os.path.getsize(output_file)
            logger.info(f"Downloaded {file_size} bytes to {output_file}")

            # Now stream this to Open Voice runtime
            # This would require integration with test_harness.py
            logger.info("Audio file ready for streaming to runtime")
            logger.info(f"Use: python test_scenarios.py with {output_file}")

            return True

        except subprocess.TimeoutExpired:
            logger.error("FFmpeg timed out")
            return False
        except Exception as e:
            logger.error(f"Error streaming HLS: {e}")
            return False

    async def test_with_stream(self, stream_name: str, duration: float = 30.0):
        """Test with a specific stream"""
        if stream_name not in HLS_TEST_STREAMS:
            logger.error(f"Unknown stream: {stream_name}")
            logger.info(f"Available: {list(HLS_TEST_STREAMS.keys())}")
            return False

        url = HLS_TEST_STREAMS[stream_name]
        logger.info(f"Testing with {stream_name}: {url}")

        return await self.stream_hls_to_runtime(url, duration)

    async def test_all_streams(self, duration: float = 10.0):
        """Test with all available streams"""
        results = {}

        for name, url in HLS_TEST_STREAMS.items():
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Testing: {name}")
            logger.info("=" * 60)

            success = await self.test_with_stream(name, duration)
            results[name] = "✓ PASS" if success else "✗ FAIL"

            await asyncio.sleep(1)  # Brief pause between tests

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("HLS STREAM TEST SUMMARY")
        logger.info("=" * 60)
        for name, result in results.items():
            logger.info(f"{result} {name}")

        return results

    def cleanup(self):
        """Clean up temporary files"""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
            logger.info(f"Cleaned up: {self.temp_dir}")

    async def play_stream_locally(self, hls_url: str, duration: float = 10.0):
        """Play HLS stream locally for manual testing"""
        cmd = [
            "ffplay",
            "-nodisp",  # No display window
            "-autoexit",  # Exit after playback
            "-t",
            str(duration),  # Duration
            hls_url,
        ]

        logger.info(f"Playing stream: {hls_url}")
        logger.info("Press Ctrl+C to stop")

        try:
            subprocess.run(cmd, timeout=duration + 5)
        except KeyboardInterrupt:
            logger.info("Playback stopped by user")
        except Exception as e:
            logger.error(f"Error playing stream: {e}")


def list_available_streams():
    """Display all available test streams"""
    print("\nAvailable HLS Test Streams:")
    print("=" * 60)
    for name, url in HLS_TEST_STREAMS.items():
        print(f"\n{name}:")
        print(f"  URL: {url}")
    print()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print(
            "  python hls_stream_tester.py list              # List available streams"
        )
        print("  python hls_stream_tester.py play <stream>     # Play stream locally")
        print(
            "  python hls_stream_tester.py download <stream> # Download stream for testing"
        )
        print("  python hls_stream_tester.py test-all          # Test all streams")
        print("\nExample:")
        print("  python hls_stream_tester.py play radio_mast_mp3")
        sys.exit(1)

    command = sys.argv[1]
    tester = HLSStreamTester()

    try:
        if command == "list":
            list_available_streams()

        elif command == "play":
            if len(sys.argv) < 3:
                print("Error: Stream name required")
                list_available_streams()
                sys.exit(1)

            stream_name = sys.argv[2]
            duration = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0

            if stream_name not in HLS_TEST_STREAMS:
                print(f"Error: Unknown stream '{stream_name}'")
                list_available_streams()
                sys.exit(1)

            url = HLS_TEST_STREAMS[stream_name]
            asyncio.run(tester.play_stream_locally(url, duration))

        elif command == "download":
            if len(sys.argv) < 3:
                print("Error: Stream name required")
                list_available_streams()
                sys.exit(1)

            stream_name = sys.argv[2]
            duration = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0

            success = asyncio.run(tester.test_with_stream(stream_name, duration))
            sys.exit(0 if success else 1)

        elif command == "test-all":
            duration = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
            asyncio.run(tester.test_all_streams(duration))

        else:
            print(f"Unknown command: {command}")
            sys.exit(1)

    finally:
        tester.cleanup()
