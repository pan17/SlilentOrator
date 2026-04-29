import asyncio
import edge_tts
import aiohttp
import socket
import tempfile
import os
import logging
import hashlib
import threading
import time
from pathlib import Path
from typing import Optional, Callable, Coroutine
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

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural", rate: str = "+0%", volume: str = "+0%", cache_dir: Optional[Path] = None):
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.state = TTSState.IDLE
        self.current_text: Optional[str] = None
        self.current_audio_file: Optional[str] = None
        self._cache_dir = cache_dir or (Path(tempfile.gettempdir()) / "silent_orator_tts")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._on_state_change: Optional[Callable] = None
        self._playback_monitor_running = False
        self._playback_monitor_thread: Optional[threading.Thread] = None

        # 初始化VLC
        if VLC_AVAILABLE:
            self._vlc_instance = vlc.Instance("--quiet")
            self._player = self._vlc_instance.media_player_new()
        else:
            self._vlc_instance = None
            self._player = None

    def set_cache_dir(self, cache_dir: Path) -> None:
        """设置音频缓存目录，用于按项目隔离缓存"""
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"TTS缓存目录已切换: {cache_dir}")
    
    def set_on_state_change(self, callback: Callable[[TTSState], None]) -> None:
        """设置状态变更回调"""
        self._on_state_change = callback

    # ==================== 页面缓存管理 ====================

    def _page_path(self, page_num: int) -> Path:
        """按页码返回缓存音频路径"""
        return self._cache_dir / f"page_{page_num:03d}.mp3"

    @staticmethod
    def _is_mp3_valid(file_path: Path) -> bool:
        """检查MP3文件是否完整有效（大小 & 文件头）"""
        if not file_path.exists():
            return False
        size = file_path.stat().st_size
        if size < 1024:
            return False
        # 检查MP3文件头
        try:
            with open(file_path, 'rb') as f:
                header = f.read(3)
                # ID3 tag 或以 0xFF 开头的MPEG帧同步
                return header in (b'ID3',) or (header[0] == 0xFF and (header[1] & 0xE0) == 0xE0)
        except Exception:
            return size > 10240  # 无法读取头时，大于10KB也算有效

    async def pre_generate_page(self, page_num: int, text: str) -> bool:
        """预生成单页音频（不触发状态变更），返回是否成功"""
        output_path = self._page_path(page_num)
        if output_path.exists() and self._is_mp3_valid(output_path):
            return True  # 已存在且有效

        try:
            connector = aiohttp.TCPConnector(resolver=None, family=socket.AF_INET)
            communicate = edge_tts.Communicate(
                text, self.voice,
                rate=self.rate, volume=self.volume,
                connector=connector
            )
            await communicate.save(str(output_path))
            logger.info(f"预生成第{page_num}页音频完成")
            return True
        except Exception as e:
            logger.error(f"预生成第{page_num}页音频失败: {e}")
            return False

    async def pre_generate_all(self, pages: dict[int, str], progress_callback=None) -> dict:
        """预生成所有页面的音频，已生成且有效的跳过。
        progress_callback: async callable(total, done, failed, skipped, page_num)
        返回: {total, done, failed, skipped}
        """
        total = len(pages)
        done = failed = skipped = 0
        for page_num in sorted(pages.keys()):
            text = pages[page_num]
            if not text or not text.strip():
                skipped += 1
                if progress_callback:
                    await progress_callback(total, done, failed, skipped, page_num)
                continue

            cache_path = self._page_path(page_num)
            if cache_path.exists() and self._is_mp3_valid(cache_path):
                skipped += 1
                if progress_callback:
                    await progress_callback(total, done, failed, skipped, page_num)
                continue

            if await self.pre_generate_page(page_num, text):
                done += 1
            else:
                failed += 1

            if progress_callback:
                await progress_callback(total, done, failed, skipped, page_num)

        logger.info(f"预生成结束: 总计{total}, 新增{done}, 失败{failed}, 跳过{skipped}")
        return {"total": total, "done": done, "failed": failed, "skipped": skipped}

    async def play_page(self, page_num: int, text: str) -> bool:
        """按页播放：优先使用缓存音频，无缓存则生成后播放"""
        if not VLC_AVAILABLE:
            logger.error("VLC不可用，无法播放")
            return False

        try:
            if self.state in (TTSState.PLAYING, TTSState.PAUSED):
                self.stop()

            self.current_text = text
            output_path = self._page_path(page_num)

            # 无缓存 → 生成
            if not output_path.exists() or not self._is_mp3_valid(output_path):
                self._set_state(TTSState.GENERATING)
                try:
                    connector = aiohttp.TCPConnector(resolver=None, family=socket.AF_INET)
                    communicate = edge_tts.Communicate(
                        text, self.voice,
                        rate=self.rate, volume=self.volume,
                        connector=connector
                    )
                    await communicate.save(str(output_path))
                except Exception as e:
                    logger.error(f"第{page_num}页音频生成失败: {e}")
                    self._set_state(TTSState.IDLE)
                    return False

            # 播放缓存
            self.current_audio_file = str(output_path)
            media = self._vlc_instance.media_new(str(output_path))
            self._player.set_media(media)
            self._player.play()
            self._set_state(TTSState.PLAYING)
            self._start_playback_monitor()
            logger.info(f"开始播放第{page_num}页: {text[:50]}...")
            return True

        except Exception as e:
            logger.error(f"播放第{page_num}页失败: {e}")
            self._set_state(TTSState.IDLE)
            return False

    def get_cache_status(self, total_pages: int) -> dict:
        """获取所有页面的音频缓存状态"""
        pages = {}
        cached_count = 0
        valid_count = 0
        for p in range(1, total_pages + 1):
            path = self._page_path(p)
            exists = path.exists()
            valid = self._is_mp3_valid(path) if exists else False
            pages[str(p)] = {
                "exists": exists,
                "valid": valid,
                "size": path.stat().st_size if exists else 0
            }
            if exists:
                cached_count += 1
                if valid:
                    valid_count += 1
        return {
            "pages": pages,
            "total": total_pages,
            "cached": cached_count,
            "valid": valid_count
        }

    def delete_page_cache(self, page_num: int) -> bool:
        """删除指定页的缓存音频"""
        path = self._page_path(page_num)
        if path.exists():
            try:
                path.unlink()
                logger.info(f"删除第{page_num}页缓存: {path}")
                return True
            except Exception as e:
                logger.error(f"删除第{page_num}页缓存失败: {e}")
                return False
        return True

    def delete_all_cache(self) -> bool:
        """删除当前项目所有缓存音频"""
        try:
            deleted = 0
            if self._cache_dir.exists():
                for f in self._cache_dir.glob("page_*.mp3"):
                    f.unlink()
                    deleted += 1
            logger.info(f"已删除{deleted}个缓存文件")
            return True
        except Exception as e:
            logger.error(f"删除全部缓存失败: {e}")
            return False

    def _set_state(self, state: TTSState) -> None:
        """设置状态并触发回调"""
        self.state = state
        if self._on_state_change:
            try:
                self._on_state_change(state)
            except Exception as e:
                logger.error(f"状态回调错误: {e}")

    def _start_playback_monitor(self) -> None:
        """启动播放结束监控线程"""
        self._stop_playback_monitor()
        self._playback_monitor_running = True

        def monitor():
            while self._playback_monitor_running:
                time.sleep(0.5)
                if not self._playback_monitor_running:
                    break
                if self._player and self.state == TTSState.PLAYING:
                    # is_playing() == 0 means playback finished
                    if self._player.is_playing() == 0:
                        logger.info("播放结束检测到，自动切换为idle")
                        self._playback_monitor_running = False
                        self._set_state(TTSState.IDLE)
                        break

        self._playback_monitor_thread = threading.Thread(target=monitor, daemon=True)
        self._playback_monitor_thread.start()

    def _stop_playback_monitor(self) -> None:
        """停止播放结束监控线程"""
        self._playback_monitor_running = False
        if self._playback_monitor_thread and self._playback_monitor_thread.is_alive():
            self._playback_monitor_thread.join(timeout=1)
        self._playback_monitor_thread = None
    
    async def generate_audio(self, text: str) -> str:
        """生成音频文件，返回文件路径"""
        self._set_state(TTSState.GENERATING)
        
        # 生成唯一文件名
        text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        output_file = str(self._cache_dir / f"tts_{text_hash}.mp3")
        
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
            self._start_playback_monitor()
            
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
                self._stop_playback_monitor()
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
                self._start_playback_monitor()
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
            self._stop_playback_monitor()
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
        """停止播放器（保留缓存音频文件）"""
        try:
            if self._player:
                self._player.stop()
        except Exception as e:
            logger.error(f"清理失败: {e}")
