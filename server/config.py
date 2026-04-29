from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    # Server config
    host: str = "0.0.0.0"
    port: int = 8000
    
    # Paths
    scripts_dir: Path = Path(__file__).parent.parent / "scripts"
    temp_audio_dir: Path = Path(__file__).parent / "temp_audio"
    
    # TTS config
    tts_voice: str = "zh-CN-XiaoxiaoNeural"  # edge-tts voice
    tts_rate: str = "+0%"  # speaking rate
    tts_volume: str = "+0%"  # volume
    
    # PPT config
    ppt_auto_open: bool = False  # auto open PPT if not already open
    
    class Config:
        env_file = ".env"

settings = Settings()
