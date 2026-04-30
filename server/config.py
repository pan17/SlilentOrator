from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    # Server config
    host: str = "0.0.0.0"
    port: int = 8000
    
    # Paths
    ppt_resource_dir: Path = Path(__file__).parent.parent / "pptresource"
    temp_audio_dir: Path = Path(__file__).parent / "temp_audio"
    
    # TTS config
    tts_engine: str = "edge-tts"  # "edge-tts" or "omnivoice"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"  # edge-tts voice
    tts_rate: str = "+0%"  # speaking rate
    tts_volume: str = "+0%"  # volume

    # Omnivoice settings
    omnivoice_url: str = "http://localhost:8001"
    omnivoice_ref_audio: str = ""  # 参考音频文件路径（必需）
    omnivoice_ref_text: str = ""  # 参考音频对应的文本（可选）
    omnivoice_lang: str = "Auto"
    omnivoice_instruct: str = ""  # 合成指令（可选，默认使用合成文本）
    omnivoice_ns: float = 32
    omnivoice_gs: float = 2.0
    omnivoice_dn: bool = True
    omnivoice_sp: float = 1.0
    omnivoice_du: float = 60  # 合成时长上限（秒）
    omnivoice_pp: bool = True
    omnivoice_po: bool = True
    
    # PPT config
    ppt_auto_open: bool = False  # auto open PPT if not already open
    
    class Config:
        env_file = ".env"

settings = Settings()
