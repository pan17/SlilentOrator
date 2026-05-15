from pydantic_settings import BaseSettings
from pathlib import Path

# 项目根目录
_proj_root = Path(__file__).parent.parent

# 自动发现 ref/ 目录下的参考音频和文本
def _discover_ref_audio() -> str:
    """自动查找 ref/ 目录下的音频文件（wav/mp3）"""
    ref_dir = _proj_root / "ref"
    if not ref_dir.exists():
        return ""
    for f in sorted(ref_dir.iterdir()):
        if f.suffix.lower() in (".wav", ".mp3", ".flac", ".ogg"):
            return str(f)
    return ""

def _discover_ref_text() -> str:
    """自动查找 ref/ 目录下的文本文件并读取内容"""
    ref_dir = _proj_root / "ref"
    if not ref_dir.exists():
        return ""
    for f in sorted(ref_dir.iterdir()):
        if f.suffix.lower() == ".txt":
            try:
                return f.read_text(encoding="utf-8").strip()
            except Exception:
                return ""
    return ""

class Settings(BaseSettings):
    # Server config
    host: str = "0.0.0.0"
    port: int = 8000
    
    # Paths
    ppt_resource_dir: Path = _proj_root / "pptresource"
    temp_audio_dir: Path = Path(__file__).parent / "temp_audio"
    
    # TTS config
    tts_engine: str = "edge-tts"  # "edge-tts" or "omnivoice"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"  # edge-tts voice
    tts_rate: str = "+0%"  # speaking rate
    tts_volume: str = "+0%"  # volume

    # Omnivoice settings
    omnivoice_url: str = "http://192.100.200.73:8001"
    omnivoice_ref_audio: str = _discover_ref_audio()
    omnivoice_ref_text: str = _discover_ref_text()
    omnivoice_lang: str = "Auto"
    omnivoice_instruct: str = ""  # 合成指令（可选）
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
