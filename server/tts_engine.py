import asyncio
import edge_tts
import aiohttp
import socket
import tempfile
import os
import logging
import hashlib
from pathlib import Path
from typing import Optional, Callable
from enum import Enum

try:
    import vlc
    VLC_AVAILABLE = True
except ImportError:
    VLC_AVAILABLE = False
    logging.warning("python-vlc未安装，TTS功能将不可用")

logger = logging.getLogger(__name__)

class TTSState(Enum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    GENERATING = "generating"

class TTSEngine:
    """基于edge-tts的TTS引擎，支持播放/暂停/继续/停止"""
    
    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural", rate: str = "+0%", volume: str = "+0%"):
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.state = TTSState.IDLE
        self.current_text: Optional[str] = None
        self.current_audio_file: Optional[str] = None
        self._temp_dir = Path(tempfile.gettempdir()) / "silent_orator_tts"
        self._temp_dir.mkdir(exist_ok=True)
        self._on_state_change: Optional[Callable] = None
        
        # 初始化VLC
        if VLC_AVAILABLE:
            self._vlc_instance = vlc.Instance("--quiet")
            self._player = self._vlc_instance.media_player_new()
        else:
            self._vlc_instance = None
            self._player = None
    
    def set_on_state_change(self, callback: Callable[[TTSState], None]) -> None:
        """设置状态变更回调"""
        self._on_state_change = callback
    
    def _set_state(self, state: TTSState) -> None:
        """设置状态并触发回调"""
        self.state = state
        if self._on_state_change:
            try:
                self._on_state_change(state)
            except Exception as e:
                logger.error(f"状态回调错误: {e}")
    
    async def generate_audio(self, text: str) -> str:
        """生成音频文件，返回文件路径"""
        self._set_state(TTSState.GENERATING)
        
        # 生成唯一文件名
        text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        output_file = str(self._temp_dir / f"tts_{text_hash}.mp3")
        
        # 如果文件已存在，直接复用
        if os.path.exists(output_file):
            logger.info(f"复用已生成的音频: {output_file}")
            return output_file
        
        try:
            # 强制使用系统DNS解析器，避免Windows下aiodns解析失败
            connector = aiohttp.TCPConnector(resolver=None, family=socket.AF_INET)
            communicate = edge_tts.Communicate(
                text, self.voice,
                rate=self.rate, volume=self.volume,
                connector=connector
            )
            await communicate.save(output_file)
            logger.info(f"音频生成完成: {output_file}")
            return output_file
        except Exception as e:
            logger.error(f"音频生成失败: {e}")
            self._set_state(TTSState.IDLE)
            raise
    
    async def play(self, text: str) -> bool:
        """播放文本，如果正在播放则先停止"""
        if not VLC_AVAILABLE:
            logger.error("VLC不可用，无法播放")
            return False
        
        try:
            # 如果正在播放，先停止
            if self.state == TTSState.PLAYING or self.state == TTSState.PAUSED:
                self.stop()
            
            self.current_text = text
            self._set_state(TTSState.GENERATING)
            
            # 生成音频
            audio_file = await self.generate_audio(text)
            self.current_audio_file = audio_file
            
            # 使用VLC播放
            media = self._vlc_instance.media_new(audio_file)
            self._player.set_media(media)
            self._player.play()
            self._set_state(TTSState.PLAYING)
            
            logger.info(f"开始播放: {text[:50]}...")
            return True
            
        except Exception as e:
            logger.error(f"播放失败: {e}")
            self._set_state(TTSState.IDLE)
            return False
    
    def pause(self) -> bool:
        """暂停播放"""
        if not self._player:
            return False
        
        try:
            if self.state == TTSState.PLAYING:
                self._player.pause()
                self._set_state(TTSState.PAUSED)
                logger.info("播放已暂停")
                return True
            return False
        except Exception as e:
            logger.error(f"暂停失败: {e}")
            return False
    
    def resume(self) -> bool:
        """继续播放"""
        if not self._player:
            return False
        
        try:
            if self.state == TTSState.PAUSED:
                self._player.pause()  # VLC中pause是切换状态
                self._set_state(TTSState.PLAYING)
                logger.info("播放已继续")
                return True
            return False
        except Exception as e:
            logger.error(f"继续播放失败: {e}")
            return False
    
    def stop(self) -> bool:
        """停止播放"""
        if not self._player:
            return False
        
        try:
            self._player.stop()
            self._set_state(TTSState.STOPPED)
            logger.info("播放已停止")
            return True
        except Exception as e:
            logger.error(f"停止失败: {e}")
            return False
    
    def is_playing(self) -> bool:
        """检查是否正在播放"""
        if not self._player:
            return False
        return self._player.is_playing() == 1 and self.state == TTSState.PLAYING
    
    def get_progress(self) -> float:
        """获取播放进度（0-1）"""
        if not self._player or self.state == TTSState.IDLE:
            return 0.0
        
        try:
            length = self._player.get_length()
            if length <= 0:
                return 0.0
            
            time = self._player.get_time()
            progress = time / length
            return max(0.0, min(1.0, progress))
        except:
            return 0.0
    
    def get_status(self) -> dict:
        """获取TTS状态"""
        return {
            "state": self.state.value,
            "is_playing": self.is_playing(),
            "current_text": self.current_text[:100] + "..." if self.current_text and len(self.current_text) > 100 else self.current_text,
            "progress": self.get_progress()
        }
    
    def cleanup(self) -> None:
        """清理临时文件"""
        try:
            if self._player:
                self._player.stop()
            
            # 清理临时音频文件
            if self._temp_dir.exists():
                for f in self._temp_dir.glob("*.mp3"):
                    try:
                        f.unlink()
                    except:
                        pass
        except Exception as e:
            logger.error(f"清理失败: {e}")
