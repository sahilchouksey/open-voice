# Audio generators for testing interruption handling
import numpy as np
import io
import wave
from typing import Generator, Optional
import logging

logger = logging.getLogger(__name__)


class AudioGenerator:
    """Generate synthetic audio for testing"""

    SAMPLE_RATE = 16000
    SAMPLE_WIDTH = 2  # 16-bit
    CHANNELS = 1

    @staticmethod
    def generate_silence(duration_sec: float) -> bytes:
        """Generate silent audio"""
        num_samples = int(AudioGenerator.SAMPLE_RATE * duration_sec)
        audio = np.zeros(num_samples, dtype=np.int16)
        return audio.tobytes()

    @staticmethod
    def generate_tone(
        frequency: float, duration_sec: float, amplitude: float = 0.5
    ) -> bytes:
        """Generate a pure tone"""
        t = np.linspace(
            0, duration_sec, int(AudioGenerator.SAMPLE_RATE * duration_sec), False
        )
        audio = amplitude * 32767 * np.sin(2 * np.pi * frequency * t)
        return audio.astype(np.int16).tobytes()

    @staticmethod
    def generate_white_noise(duration_sec: float, amplitude: float = 0.1) -> bytes:
        """Generate white noise (simulates background noise)"""
        num_samples = int(AudioGenerator.SAMPLE_RATE * duration_sec)
        audio = amplitude * 32767 * np.random.normal(0, 1, num_samples)
        return audio.astype(np.int16).tobytes()

    @staticmethod
    def generate_chirp(
        start_freq: float, end_freq: float, duration_sec: float
    ) -> bytes:
        """Generate frequency sweep (chirp)"""
        t = np.linspace(
            0, duration_sec, int(AudioGenerator.SAMPLE_RATE * duration_sec), False
        )
        # Exponential frequency sweep
        freqs = start_freq * (end_freq / start_freq) ** (t / duration_sec)
        phase = 2 * np.pi * np.cumsum(freqs) / AudioGenerator.SAMPLE_RATE
        audio = 0.5 * 32767 * np.sin(phase)
        return audio.astype(np.int16).tobytes()

    @staticmethod
    def generate_speech_pattern(
        pattern_type: str = "natural", duration_sec: float = 5.0
    ) -> bytes:
        """
        Generate synthetic speech-like audio pattern

        pattern_type: 'natural', 'continuous', 'staccato', 'noisy'
        """
        num_samples = int(AudioGenerator.SAMPLE_RATE * duration_sec)
        audio = np.zeros(num_samples, dtype=np.float32)

        if pattern_type == "natural":
            # Simulate natural speech with pauses
            pos = 0
            while pos < num_samples:
                # Speech burst
                burst_duration = int(
                    AudioGenerator.SAMPLE_RATE * np.random.uniform(0.2, 0.8)
                )
                burst_end = min(pos + burst_duration, num_samples)

                # Generate speech-like sound (modulated noise)
                t = np.arange(burst_end - pos) / AudioGenerator.SAMPLE_RATE
                freq = np.random.uniform(150, 400)  # Formant frequency
                modulator = 0.5 + 0.5 * np.sin(2 * np.pi * 5 * t)  # 5Hz modulation
                signal = np.sin(2 * np.pi * freq * t) * modulator
                signal += 0.3 * np.random.normal(0, 1, len(signal))  # Add noise

                audio[pos:burst_end] = signal * 0.3
                pos = burst_end

                # Pause
                pause_duration = int(
                    AudioGenerator.SAMPLE_RATE * np.random.uniform(0.1, 0.4)
                )
                pos += pause_duration

        elif pattern_type == "continuous":
            # Continuous speech without pauses (chain reaction scenario)
            t = np.arange(num_samples) / AudioGenerator.SAMPLE_RATE
            base_freq = 200
            # Modulate between frequencies to simulate continuous talking
            freq_mod = base_freq + 50 * np.sin(2 * np.pi * 0.5 * t)
            phase = np.cumsum(2 * np.pi * freq_mod / AudioGenerator.SAMPLE_RATE)
            audio = 0.4 * np.sin(phase)
            # Add harmonic content
            audio += 0.2 * np.sin(2 * phase)
            audio += 0.1 * np.random.normal(0, 1, num_samples)

        elif pattern_type == "staccato":
            # Short, sharp bursts (rapid interruptions)
            pos = 0
            while pos < num_samples:
                burst_duration = int(AudioGenerator.SAMPLE_RATE * 0.1)  # 100ms bursts
                burst_end = min(pos + burst_duration, num_samples)

                freq = np.random.uniform(300, 600)
                t = np.arange(burst_end - pos) / AudioGenerator.SAMPLE_RATE
                signal = np.sin(2 * np.pi * freq * t)
                # Sharp attack
                envelope = np.minimum(
                    np.arange(len(t)) / (0.01 * AudioGenerator.SAMPLE_RATE), 1.0
                )
                envelope *= np.exp(-t * 10)  # Quick decay

                audio[pos:burst_end] = signal * envelope * 0.5
                pos = burst_end

                # Short pause
                pos += int(AudioGenerator.SAMPLE_RATE * np.random.uniform(0.05, 0.2))

        elif pattern_type == "noisy":
            # Speech with high background noise (false positive test)
            t = np.arange(num_samples) / AudioGenerator.SAMPLE_RATE
            # Base speech signal
            speech = 0.3 * np.sin(2 * np.pi * 250 * t)
            # Heavy background noise
            noise = 0.8 * np.random.normal(0, 1, num_samples)
            audio = speech + noise

        # Normalize and convert to int16
        max_val = np.max(np.abs(audio))
        if max_val > 0:
            audio = audio / max_val * 0.5 * 32767

        return audio.astype(np.int16).tobytes()

    @staticmethod
    def create_wav_file(audio_data: bytes, filename: str):
        """Save audio data as WAV file"""
        with wave.open(filename, "wb") as wav_file:
            wav_file.setnchannels(AudioGenerator.CHANNELS)
            wav_file.setsampwidth(AudioGenerator.SAMPLE_WIDTH)
            wav_file.setframerate(AudioGenerator.SAMPLE_RATE)
            wav_file.writeframes(audio_data)
        logger.info(f"Saved audio to {filename}")

    @staticmethod
    def load_wav_file(filename: str) -> Optional[bytes]:
        """Load audio from WAV file"""
        try:
            with wave.open(filename, "rb") as wav_file:
                if (
                    wav_file.getnchannels() != AudioGenerator.CHANNELS
                    or wav_file.getsampwidth() != AudioGenerator.SAMPLE_WIDTH
                    or wav_file.getframerate() != AudioGenerator.SAMPLE_RATE
                ):
                    logger.warning(f"WAV file format mismatch: {filename}")

                return wav_file.readframes(wav_file.getnframes())
        except Exception as e:
            logger.error(f"Failed to load {filename}: {e}")
            return None


class AudioStreamSimulator:
    """Simulate streaming audio with various patterns"""

    def __init__(self, chunk_duration_ms: float = 100):
        self.chunk_duration_ms = chunk_duration_ms
        self.chunk_samples = int(AudioGenerator.SAMPLE_RATE * chunk_duration_ms / 1000)

    def stream_audio(
        self, audio_data: bytes, simulate_latency_ms: float = 0
    ) -> Generator[bytes, None, None]:
        """
        Stream audio data in chunks

        Args:
            audio_data: Complete audio data
            simulate_latency_ms: Artificial latency between chunks (for testing)
        """
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        num_chunks = len(audio_array) // self.chunk_samples

        for i in range(num_chunks):
            start = i * self.chunk_samples
            end = start + self.chunk_samples
            chunk = audio_array[start:end].tobytes()

            if simulate_latency_ms > 0:
                time.sleep(simulate_latency_ms / 1000)

            yield chunk

        # Yield remaining samples
        remaining = audio_array[num_chunks * self.chunk_samples :]
        if len(remaining) > 0:
            yield remaining.tobytes()

    def create_interruption_scenario(
        self,
        assistant_duration: float = 10.0,
        interruption_delay: float = 3.0,
        interruption_duration: float = 2.0,
    ) -> tuple:
        """
        Create audio data for interruption testing scenario

        Returns:
            Tuple of (assistant_audio, user_audio, interruption_start_sample)
        """
        # Generate assistant speaking
        assistant_audio = AudioGenerator.generate_speech_pattern(
            "continuous", assistant_duration
        )

        # Generate user interruption
        user_audio = AudioGenerator.generate_speech_pattern(
            "natural", interruption_duration
        )

        # Calculate when interruption starts
        interruption_start_sample = int(AudioGenerator.SAMPLE_RATE * interruption_delay)

        return assistant_audio, user_audio, interruption_start_sample


# Predefined test audio patterns
TEST_PATTERNS = {
    "silence_1s": lambda: AudioGenerator.generate_silence(1.0),
    "silence_5s": lambda: AudioGenerator.generate_silence(5.0),
    "tone_1khz": lambda: AudioGenerator.generate_tone(1000, 2.0),
    "tone_500hz": lambda: AudioGenerator.generate_tone(500, 2.0),
    "chirp": lambda: AudioGenerator.generate_chirp(200, 800, 3.0),
    "white_noise": lambda: AudioGenerator.generate_white_noise(5.0, 0.2),
    "speech_natural": lambda: AudioGenerator.generate_speech_pattern("natural", 5.0),
    "speech_continuous": lambda: AudioGenerator.generate_speech_pattern(
        "continuous", 10.0
    ),
    "speech_staccato": lambda: AudioGenerator.generate_speech_pattern("staccato", 5.0),
    "speech_noisy": lambda: AudioGenerator.generate_speech_pattern("noisy", 5.0),
}


def get_test_audio(pattern_name: str) -> bytes:
    """Get predefined test audio by name"""
    if pattern_name in TEST_PATTERNS:
        return TEST_PATTERNS[pattern_name]()
    else:
        raise ValueError(
            f"Unknown pattern: {pattern_name}. Available: {list(TEST_PATTERNS.keys())}"
        )


if __name__ == "__main__":
    # Test audio generation
    import os

    os.makedirs("test_audio", exist_ok=True)

    print("Generating test audio files...")
    for name, generator in TEST_PATTERNS.items():
        audio = generator()
        filename = f"test_audio/{name}.wav"
        AudioGenerator.create_wav_file(audio, filename)
        print(f"  ✓ {filename}")

    print("\nTest audio generation complete!")
