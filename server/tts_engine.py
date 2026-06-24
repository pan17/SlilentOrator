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
import shutil
from pathlib import Path
from typing import Optional, Callable, Coroutine
from enum import Enum

try:
    import vlc
    VLC_AVAILABLE = True
except ImportError:
    VLC_AVAILABLE = False
    logging.warning("python-vlc未安装，TTS功能将不可用")

try:
    from gradio_client import Client, handle_file
    GRADIO_CLIENT_AVAILABLE = True
except ImportError:
    GRADIO_CLIENT_AVAILABLE = False
    logging.warning("gradio-client未安装，Omnivoice引擎不可用")

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
        """检查音频文件是否有效（MP3/WAV，大小 & 文件头）"""
        if not file_path.exists():
            return False
        size = file_path.stat().st_size
        if size < 1024:
            return False
        try:
            with open(file_path, 'rb') as f:
                header = f.read(4)
            # MP3: ID3 tag 或 MPEG 帧同步 (0xFF)
            if len(header) >= 3:
                if header[:3] == b'ID3' or (header[0] == 0xFF and (header[1] & 0xE0) == 0xE0):
                    return True
            # WAV: RIFF 头
            if len(header) >= 4 and header[:4] == b'RIFF':
                return True
            return False
        except Exception:
            return size > 10240

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


class OmnivoiceEngine:
    """基于Omnivoice本地语音克隆的TTS引擎，通过Gradio Client调用本地部署的API"""

    def __init__(
        self,
        url: str = "http://localhost:8001",
        ref_aud: str = "",
        ref_text: str = "",
        lang: str = "Auto",
        instruct: str = "",
        ns: float = 32,
        gs: float = 2.0,
        dn: bool = True,
        sp: float = 1.0,
        du: float = 60,
        pp: bool = True,
        po: bool = True,
        cache_dir: Optional[Path] = None,
    ):
        self.url = url
        self.ref_aud = self._sanitize_path(ref_aud)
        self.ref_text = ref_text
        self.lang = lang
        self.instruct = instruct
        self.ns = ns
        self.gs = gs
        self.dn = dn
        self.sp = sp
        self.du = du
        self.pp = pp
        self.po = po

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

        # 初始化Gradio Client
        self._gradio_client: Optional["Client"] = None
        if GRADIO_CLIENT_AVAILABLE:
            self._init_gradio_client()

    def _init_gradio_client(self) -> None:
        """初始化Gradio客户端连接"""
        try:
            self._gradio_client = Client(self.url)
            logger.info(f"Omnivoice Gradio客户端已连接: {self.url}")
        except Exception as e:
            logger.error(f"Omnivoice Gradio客户端初始化失败: {e}")
            self._gradio_client = None

    @staticmethod
    def _sanitize_path(path: str) -> str:
        """去除路径中的不可见Unicode控制字符（如U+202A等）"""
        if not path:
            return path
        # 移除方向控制字符、零宽字符等
        control_chars = ''.join(c for c in path if ord(c) in range(0x200B, 0x200F) or ord(c) in range(0x202A, 0x202F) or ord(c) in (0xFEFF,))
        cleaned = path
        for c in control_chars:
            cleaned = cleaned.replace(c, '')
        return cleaned.strip()

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
        """检查音频文件是否有效（MP3/WAV，大小 & 文件头）"""
        if not file_path.exists():
            return False
        size = file_path.stat().st_size
        if size < 1024:
            return False
        try:
            with open(file_path, 'rb') as f:
                header = f.read(4)
            # MP3: ID3 tag 或 MPEG 帧同步 (0xFF)
            if len(header) >= 3:
                if header[:3] == b'ID3' or (header[0] == 0xFF and (header[1] & 0xE0) == 0xE0):
                    return True
            # WAV: RIFF 头
            if len(header) >= 4 and header[:4] == b'RIFF':
                return True
            return False
        except Exception:
            return size > 10240

    async def _predict_omnivoice(self, text: str) -> str:
        """调用Omnivoice API合成音频，返回临时音频文件路径"""
        if not self._gradio_client:
            raise RuntimeError("Omnivoice Gradio客户端未初始化，请检查服务是否运行")

        if not self.ref_aud:
            raise ValueError("Omnivoice引擎需要配置参考音频路径 (ref_aud)")

        if not os.path.exists(self.ref_aud):
            raise FileNotFoundError(f"参考音频文件不存在: {self.ref_aud}")

        # du=0 表示让 Omnivoice 根据 speed 参数自动计算时长
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._gradio_client.predict(
                text=text,
                lang=self.lang,
                ref_aud=handle_file(self.ref_aud),
                ref_text=self.ref_text if self.ref_text else "",
                instruct=self.instruct if self.instruct else "",
                ns=self.ns,
                gs=self.gs,
                dn=self.dn,
                sp=self.sp,
                du=0,
                pp=self.pp,
                po=self.po,
                api_name="/_clone_fn",
            )
        )
        logger.info(f"Omnivoice预测: text_len={len(text)}, sp={self.sp}")

        if result is None:
            raise RuntimeError("Omnivoice返回空结果")

        # 解析返回值
        if isinstance(result, tuple) and len(result) >= 2:
            audio_raw, status_text = result[0], result[1]
        else:
            audio_raw = result
            status_text = ""

        # 处理 FileData 对象
        if hasattr(audio_raw, 'path'):
            audio_path = audio_raw.path
        else:
            audio_path = audio_raw

        if not audio_path:
            raise RuntimeError(f"Omnivoice返回的音频路径为空, status: {status_text}")

        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Omnivoice生成的临时音频不存在: {audio_path}")

        return audio_path

    async def pre_generate_page(self, page_num: int, text: str) -> bool:
        """预生成单页音频（不触发状态变更），返回是否成功"""
        output_path = self._page_path(page_num)
        if output_path.exists() and self._is_mp3_valid(output_path):
            return True

        try:
            temp_path = await self._predict_omnivoice(text)
            shutil.copy2(temp_path, output_path)
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
                    temp_path = await self._predict_omnivoice(text)
                    shutil.copy2(temp_path, output_path)
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
            if self._cache_dir.exists():
                for f in self._cache_dir.glob("page_*.mp3"):
                    f.unlink()
            logger.info("已删除全部缓存文件")
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
        """生成音频文件（hash缓存），返回文件路径"""
        self._set_state(TTSState.GENERATING)

        text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        output_file = str(self._cache_dir / f"tts_{text_hash}.mp3")

        if os.path.exists(output_file):
            logger.info(f"复用已生成的音频: {output_file}")
            return output_file

        try:
            temp_path = await self._predict_omnivoice(text)
            shutil.copy2(temp_path, output_file)
            logger.info(f"Omnivoice音频生成完成: {output_file}")
            return output_file
        except Exception as e:
            logger.error(f"Omnivoice音频生成失败: {e}")
            self._set_state(TTSState.IDLE)
            raise

    async def play(self, text: str) -> bool:
        """播放文本，如果正在播放则先停止"""
        if not VLC_AVAILABLE:
            logger.error("VLC不可用，无法播放")
            return False

        try:
            if self.state in (TTSState.PLAYING, TTSState.PAUSED):
                self.stop()

            self.current_text = text
            self._set_state(TTSState.GENERATING)

            audio_file = await self.generate_audio(text)
            self.current_audio_file = audio_file

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
                self._player.pause()
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
            t = self._player.get_time()
            progress = t / length
            return max(0.0, min(1.0, progress))
        except Exception:
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
