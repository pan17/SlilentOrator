import time
import win32com.client
from typing import Optional, Tuple
import pythoncom
import logging

logger = logging.getLogger(__name__)

class PPTController:
    """基于pywin32的PowerPoint COM接口控制器"""

    def __init__(self):
        self.app: Optional[win32com.client.Dispatch] = None
        self.presentation = None
        self._target_slide: int = 0  # 乐观锁：预期切换到的页码
        self._init_com()

    def _init_com(self) -> None:
        """初始化COM接口"""
        try:
            pythoncom.CoInitialize()
            self.app = win32com.client.Dispatch("PowerPoint.Application")
            self.app.Visible = True
            logger.info("PowerPoint COM接口初始化成功")
        except Exception as e:
            logger.error(f"PowerPoint COM接口初始化失败: {e}")
            raise RuntimeError(f"无法连接到PowerPoint，请确保已安装PowerPoint: {e}")

    def _ensure_com(self) -> bool:
        """确保COM连接有效，如果PowerPoint被关闭则自动重新连接"""
        if self.app is None:
            try:
                self._init_com()
                return True
            except Exception:
                return False
        try:
            # 检测COM是否存活（访问属性不报错说明连接正常）
            _ = self.app.Visible
            return True
        except Exception:
            logger.warning("PowerPoint已关闭，正在重新连接...")
            try:
                self.app = win32com.client.Dispatch("PowerPoint.Application")
                self.app.Visible = True
                self.presentation = None
                self._target_slide = 0
                logger.info("PowerPoint COM重新连接成功")
                return True
            except Exception as e:
                logger.error(f"PowerPoint COM重新连接失败: {e}")
                return False

    def open_presentation(self, file_path: str) -> bool:
        """打开指定PPT文件"""
        try:
            if not self._ensure_com():
                return False
            self.presentation = self.app.Presentations.Open(file_path)
            logger.info(f"打开PPT文件: {file_path}")
            return True
        except Exception as e:
            logger.error(f"打开PPT文件失败: {e}")
            return False

    def get_active_presentation(self) -> bool:
        """获取当前活动的演示文稿"""
        try:
            if not self._ensure_com():
                return False
            if self.app.Presentations.Count > 0:
                self.presentation = self.app.ActivePresentation
                return True
            return False
        except Exception as e:
            logger.error(f"获取活动演示文稿失败: {e}")
            return False

    def _in_slideshow(self) -> bool:
        """检查是否处于放映模式"""
        try:
            return self.app.SlideShowWindows.Count > 0
        except:
            return False

    def _get_view(self):
        """获取当前视图（自动适配放映/编辑模式）"""
        if self._in_slideshow():
            return self.app.SlideShowWindows(1).View
        return self.app.ActiveWindow.View

    def next_slide(self) -> bool:
        """下一页（乐观切换：不阻塞等COM稳定）"""
        try:
            if not self._ensure_presentation():
                return False

            current = self.get_current_slide()
            total = self.get_total_slides()

            if current < total:
                target = current + 1
                self._target_slide = target
                self._get_view().GotoSlide(target)
                return True
            return False
        except Exception as e:
            logger.error(f"下一页失败: {e}")
            return False

    def prev_slide(self) -> bool:
        """上一页（乐观切换）"""
        try:
            if not self._ensure_presentation():
                return False

            current = self.get_current_slide()

            if current > 1:
                target = current - 1
                self._target_slide = target
                self._get_view().GotoSlide(target)
                return True
            return False
        except Exception as e:
            logger.error(f"上一页失败: {e}")
            return False

    def goto_slide(self, slide_num: int) -> bool:
        """跳转到指定页（乐观切换）"""
        try:
            if not self._ensure_presentation():
                return False

            total = self.get_total_slides()

            if 1 <= slide_num <= total:
                self._target_slide = slide_num
                self._get_view().GotoSlide(slide_num)
                return True
            return False
        except Exception as e:
            logger.error(f"跳页失败: {e}")
            return False

    def get_current_slide(self) -> int:
        """获取当前页码（使用乐观锁避免阻塞，COM追上后回落）"""
        try:
            if not self._ensure_presentation():
                return self._target_slide or 0

            if self._in_slideshow():
                com_value = self.app.SlideShowWindows(1).View.CurrentShowPosition
            else:
                com_value = self.app.ActiveWindow.View.Slide.SlideIndex

            # COM还没追上乐观值 → 极短等待（~100ms）防止连续翻页出错
            if self._target_slide and com_value != self._target_slide:
                for _ in range(5):
                    time.sleep(0.02)
                    try:
                        if self._in_slideshow():
                            com_value = self.app.SlideShowWindows(1).View.CurrentShowPosition
                        else:
                            com_value = self.app.ActiveWindow.View.Slide.SlideIndex
                        if com_value == self._target_slide:
                            break
                    except Exception:
                        pass

            # COM已追上目标 → 清除乐观锁
            if self._target_slide and com_value == self._target_slide:
                self._target_slide = 0

            return com_value if com_value else (self._target_slide or 0)

        except Exception as e:
            logger.error(f"获取当前页码失败: {e}")
            # COM读不到时返回乐观值
            return self._target_slide or 0

    def get_total_slides(self) -> int:
        """获取总页数"""
        try:
            if not self._ensure_presentation():
                return 0
            return self.presentation.Slides.Count
        except Exception as e:
            logger.error(f"获取总页数失败: {e}")
            return 0

    def get_status(self) -> dict:
        """获取PPT状态"""
        return {
            "current_slide": self.get_current_slide(),
            "total_slides": self.get_total_slides(),
            "has_presentation": self.presentation is not None
        }

    def _ensure_presentation(self) -> bool:
        """确保有活动的演示文稿"""
        if not self._ensure_com():
            return False
        if self.presentation is None:
            return self.get_active_presentation()
        return True

    def start_slideshow(self) -> bool:
        """开始放映（从当前页）"""
        try:
            if not self._ensure_com():
                return False
            if not self._ensure_presentation():
                return False
            self.presentation.SlideShowSettings.Run()
            return True
        except Exception as e:
            logger.error(f"开始放映失败: {e}")
            return False

    def stop_slideshow(self) -> bool:
        """结束放映"""
        try:
            if not self._ensure_com():
                return False
            if self.app.SlideShowWindows.Count > 0:
                self.app.SlideShowWindows(1).View.Exit()
            return True
        except Exception as e:
            logger.error(f"结束放映失败: {e}")
            return False

    def quit(self) -> None:
        """退出PowerPoint应用"""
        try:
            if self.app:
                self.app.Quit()
        except:
            pass
        finally:
            pythoncom.CoUninitialize()
