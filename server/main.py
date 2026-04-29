import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# 确保当前目录在路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import settings
from ppt_controller import PPTController
from tts_engine import TTSEngine, TTSState
from script_parser import ScriptParser

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 全局实例
ppt_controller: Optional[PPTController] = None
tts_engine: Optional[TTSEngine] = None
script_parser: Optional[ScriptParser] = None
current_project: Optional[str] = None  # 当前加载的演讲稿项目名
_main_loop: Optional[asyncio.AbstractEventLoop] = None  # 主线程event loop，供后台线程调用

# 预生成状态
pregen_running = False
pregen_total = 0
pregen_done = 0
pregen_failed = 0
pregen_skipped = 0

# WebSocket连接管理器
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket客户端连接，当前连接数: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket客户端断开，当前连接数: {len(self.active_connections)}")
    
    async def broadcast(self, message: dict):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"发送消息失败: {e}")
                disconnected.append(connection)
        
        for conn in disconnected:
            self.disconnect(conn)

manager = ConnectionManager()

async def broadcast_status():
    """广播当前状态"""
    status = get_full_status()
    await manager.broadcast({
        "type": "status_update",
        "data": status
    })

def on_tts_state_change(state: TTSState):
    """TTS状态变更回调（可能从后台线程触发）"""
    global _main_loop
    if _main_loop is not None:
        asyncio.run_coroutine_threadsafe(broadcast_status(), _main_loop)


async def start_pre_generation():
    """后台预生成所有页面的音频"""
    global pregen_running, pregen_total, pregen_done, pregen_failed, pregen_skipped
    if not script_parser or not tts_engine or not script_parser.pages:
        logger.warning("无法预生成：演讲稿或TTS引擎未就绪")
        return

    async def on_progress(total, done, failed, skipped, page_num):
        global pregen_total, pregen_done, pregen_failed, pregen_skipped
        pregen_total = total
        pregen_done = done
        pregen_failed = failed
        pregen_skipped = skipped
        await broadcast_status()

    pregen_running = True
    pregen_total = len(script_parser.pages)
    pregen_done = 0
    pregen_failed = 0
    pregen_skipped = 0
    await broadcast_status()

    try:
        result = await tts_engine.pre_generate_all(script_parser.pages, progress_callback=on_progress)
        logger.info(f"预生成完成: {result}")
    except Exception as e:
        logger.error(f"预生成异常: {e}")
    finally:
        pregen_running = False
        await broadcast_status()

def get_full_status() -> dict:
    """获取完整状态"""
    status = {
        "project": current_project,
        "ppt": ppt_controller.get_status() if ppt_controller else {"error": "未初始化"},
        "tts": tts_engine.get_status() if tts_engine else {"error": "未初始化"},
        "script": {
            "total_pages": script_parser.get_total_pages() if script_parser else 0,
            "loaded": script_parser is not None
        },
        "pre_generation": {
            "running": pregen_running,
            "total": pregen_total,
            "done": pregen_done,
            "failed": pregen_failed,
            "skipped": pregen_skipped
        }
    }
    return status

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global ppt_controller, tts_engine, script_parser, _main_loop

    # 保存主线程event loop引用，供TTS回调跨线程调用
    _main_loop = asyncio.get_running_loop()

    logger.info("正在初始化服务...")
    
    # 初始化PPT控制器
    try:
        ppt_controller = PPTController()
        logger.info("PPT控制器初始化成功")
    except Exception as e:
        logger.error(f"PPT控制器初始化失败: {e}")
    
    # 初始化TTS引擎
    try:
        tts_engine = TTSEngine(
            voice=settings.tts_voice,
            rate=settings.tts_rate,
            volume=settings.tts_volume
        )
        tts_engine.set_on_state_change(on_tts_state_change)
        logger.info("TTS引擎初始化成功")
    except Exception as e:
        logger.error(f"TTS引擎初始化失败: {e}")
    
    # 尝试加载 pptresource 下的第一个项目（如果有）
    if settings.ppt_resource_dir.exists():
        projects = sorted([d for d in settings.ppt_resource_dir.iterdir() if d.is_dir() and not d.name.startswith('.')])
        if projects:
            first_project = projects[0]
            ppt_files = list(first_project.glob("*.pptx")) + list(first_project.glob("*.ppt"))
            script_path = first_project / "script.md"
            if ppt_files and script_path.exists():
                try:
                    ppt_controller.open_presentation(str(ppt_files[0]))
                    script_parser = ScriptParser.from_file(script_path)
                    tts_cache_dir = first_project / "tts_cache"
                    if tts_engine:
                        tts_engine.set_cache_dir(tts_cache_dir)
                    current_project = first_project.name
                    logger.info(f"已自动加载项目: {first_project.name}, 共 {script_parser.get_total_pages()} 页")
                except Exception as e:
                    logger.warning(f"自动加载项目失败: {e}")
    
    logger.info("服务初始化完成")
    yield
    
    # 清理
    logger.info("正在关闭服务...")
    if tts_engine:
        tts_engine.cleanup()
    if ppt_controller:
        ppt_controller.quit()
    logger.info("服务已关闭")

app = FastAPI(
    title="SlilentOrator Server",
    description="PPT嘴替 - Windows后台服务",
    version="1.0.0",
    lifespan=lifespan
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件（网页控制台）
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/")
async def root():
    """网页控制台入口"""
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "SlilentOrator Server"}

# ==================== 状态接口 ====================

@app.get("/api/status")
async def get_status():
    """获取当前状态"""
    return get_full_status()

# ==================== PPT控制接口 ====================

@app.post("/api/ppt/next")
async def ppt_next():
    """下一页"""
    if not ppt_controller:
        raise HTTPException(status_code=503, detail="PPT控制器未初始化")
    
    success = ppt_controller.next_slide()
    await broadcast_status()
    
    return {"success": success, "status": ppt_controller.get_status()}

@app.post("/api/ppt/prev")
async def ppt_prev():
    """上一页"""
    if not ppt_controller:
        raise HTTPException(status_code=503, detail="PPT控制器未初始化")
    
    success = ppt_controller.prev_slide()
    await broadcast_status()
    
    return {"success": success, "status": ppt_controller.get_status()}

@app.post("/api/ppt/goto/{page}")
async def ppt_goto(page: int):
    """跳转到指定页"""
    if not ppt_controller:
        raise HTTPException(status_code=503, detail="PPT控制器未初始化")
    
    success = ppt_controller.goto_slide(page)
    await broadcast_status()
    
    return {"success": success, "status": ppt_controller.get_status()}

@app.post("/api/ppt/start")
async def ppt_start():
    """开始放映"""
    if not ppt_controller:
        raise HTTPException(status_code=503, detail="PPT控制器未初始化")
    
    success = ppt_controller.start_slideshow()
    await broadcast_status()
    
    return {"success": success}

@app.post("/api/ppt/stop")
async def ppt_stop():
    """结束放映"""
    if not ppt_controller:
        raise HTTPException(status_code=503, detail="PPT控制器未初始化")
    
    success = ppt_controller.stop_slideshow()
    await broadcast_status()
    
    return {"success": success}

# ==================== 演讲项目管理接口 ====================

@app.get("/api/projects")
async def list_projects():
    """列出 pptresource 下所有演讲项目文件夹"""
    projects = []
    if settings.ppt_resource_dir.exists():
        for folder in sorted(settings.ppt_resource_dir.iterdir()):
            if folder.is_dir() and not folder.name.startswith('.'):
                # 检查是否包含脚本和PPT
                ppt_files = list(folder.glob("*.pptx")) + list(folder.glob("*.ppt"))
                has_ppt = len(ppt_files) > 0
                has_script = (folder / "script.md").exists()
                projects.append({
                    "name": folder.name,
                    "has_ppt": has_ppt,
                    "has_script": has_script,
                    "ppt_file": ppt_files[0].name if ppt_files else None
                })
    return {"projects": projects, "current": current_project}


@app.post("/api/projects/load")
async def load_project(name: str = Query(..., description="演讲稿项目名")):
    """加载指定演讲项目：打开PPT + 加载演讲稿"""
    global script_parser, current_project

    project_dir = settings.ppt_resource_dir / name
    if not project_dir.exists() or not project_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"项目不存在: {name}")

    # 查找PPT文件
    ppt_files = list(project_dir.glob("*.pptx")) + list(project_dir.glob("*.ppt"))
    if not ppt_files:
        raise HTTPException(status_code=404, detail=f"项目中未找到PPT文件: {name}")

    # 打开PPT
    if not ppt_controller:
        raise HTTPException(status_code=503, detail="PPT控制器未初始化")
    ppt_path = str(ppt_files[0])
    if not ppt_controller.open_presentation(ppt_path):
        raise HTTPException(status_code=500, detail=f"PPT文件打开失败: {ppt_files[0].name}")

    # 启动放映
    ppt_controller.start_slideshow()

    # 加载演讲稿
    script_path = project_dir / "script.md"
    if script_path.exists():
        try:
            script_parser = ScriptParser.from_file(script_path)
            logger.info(f"演讲稿加载成功: {script_path}, 共 {script_parser.get_total_pages()} 页")
        except Exception as e:
            logger.warning(f"演讲稿加载失败: {e}")
            script_parser = None

    # 切换TTS缓存目录到项目目录
    tts_cache_dir = project_dir / "tts_cache"
    if tts_engine:
        tts_engine.set_cache_dir(tts_cache_dir)

    current_project = name
    await broadcast_status()

    return {
        "success": True,
        "project": name,
        "ppt": ppt_files[0].name,
        "script_loaded": script_parser is not None
    }


# ==================== TTS控制接口 ====================

@app.post("/api/tts/play")
async def tts_play(page: Optional[int] = None):
    """播放指定页或当前页的演讲稿（使用按页缓存，无缓存则生成后播放）"""
    if not tts_engine:
        raise HTTPException(status_code=503, detail="TTS引擎未初始化")
    if not script_parser:
        raise HTTPException(status_code=503, detail="演讲稿未加载")
    
    # 如果没有指定页码，使用PPT当前页
    if page is None:
        if ppt_controller:
            page = ppt_controller.get_current_slide()
        else:
            raise HTTPException(status_code=503, detail="PPT控制器未初始化，无法获取当前页")
    
    text = script_parser.get_page(page)
    if not text:
        raise HTTPException(status_code=404, detail=f"第{page}页演讲稿不存在")
    
    success = await tts_engine.play_page(page, text)
    await broadcast_status()
    
    return {"success": success, "page": page}


@app.get("/api/tts/cache")
async def get_tts_cache_status():
    """获取所有页面的音频缓存状态"""
    if not tts_engine:
        raise HTTPException(status_code=503, detail="TTS引擎未初始化")
    if not script_parser:
        raise HTTPException(status_code=503, detail="演讲稿未加载")

    return tts_engine.get_cache_status(script_parser.get_total_pages())


@app.post("/api/tts/pre_generate")
async def pre_generate_audio():
    """后台预生成所有页面的音频（已有且有效的跳过）"""
    if not tts_engine:
        raise HTTPException(status_code=503, detail="TTS引擎未初始化")
    if not script_parser:
        raise HTTPException(status_code=503, detail="演讲稿未加载")
    if pregen_running:
        raise HTTPException(status_code=400, detail="正在预生成中，请等待完成")

    # 后台启动预生成
    asyncio.create_task(start_pre_generation())
    return {"success": True, "message": "后台预生成已启动"}


@app.post("/api/tts/delete_cache")
async def delete_tts_cache(page: Optional[int] = None):
    """删除指定页或全部音频缓存"""
    if not tts_engine:
        raise HTTPException(status_code=503, detail="TTS引擎未初始化")

    if page is not None:
        success = tts_engine.delete_page_cache(page)
        return {"success": success, "page": page}
    else:
        success = tts_engine.delete_all_cache()
        await broadcast_status()
        return {"success": success, "message": "全部缓存已删除"}

@app.post("/api/tts/pause")
async def tts_pause():
    """暂停播放"""
    if not tts_engine:
        raise HTTPException(status_code=503, detail="TTS引擎未初始化")
    
    success = tts_engine.pause()
    await broadcast_status()
    
    return {"success": success}

@app.post("/api/tts/resume")
async def tts_resume():
    """继续播放"""
    if not tts_engine:
        raise HTTPException(status_code=503, detail="TTS引擎未初始化")
    
    success = tts_engine.resume()
    await broadcast_status()
    
    return {"success": success}

@app.post("/api/tts/stop")
async def tts_stop():
    """停止播放"""
    if not tts_engine:
        raise HTTPException(status_code=503, detail="TTS引擎未初始化")
    
    success = tts_engine.stop()
    await broadcast_status()
    
    return {"success": success}

# ==================== 演讲稿接口 ====================

@app.get("/api/script/pages")
async def get_script_pages():
    """获取演讲稿所有页面内容"""
    if not script_parser:
        raise HTTPException(status_code=503, detail="演讲稿未加载")
    
    return {
        "total_pages": script_parser.get_total_pages(),
        "pages": script_parser.pages
    }

@app.get("/api/script/page/{page}")
async def get_script_page(page: int):
    """获取指定页演讲稿"""
    if not script_parser:
        raise HTTPException(status_code=503, detail="演讲稿未加载")
    
    text = script_parser.get_page(page)
    if text is None:
        raise HTTPException(status_code=404, detail=f"第{page}页不存在")
    
    return {"page": page, "text": text}

# ==================== WebSocket ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket实时状态推送"""
    await manager.connect(websocket)
    
    try:
        # 发送当前状态
        await websocket.send_json({
            "type": "status_update",
            "data": get_full_status()
        })
        
        while True:
            # 接收客户端消息
            data = await websocket.receive_json()
            action = data.get("action")
            
            if action == "next":
                if ppt_controller:
                    ppt_controller.next_slide()
            elif action == "prev":
                if ppt_controller:
                    ppt_controller.prev_slide()
            elif action == "goto":
                if ppt_controller:
                    ppt_controller.goto_slide(data.get("page", 1))
            elif action == "play":
                if tts_engine and script_parser:
                    page = data.get("page")
                    if page is None and ppt_controller:
                        page = ppt_controller.get_current_slide()
                    if page:
                        text = script_parser.get_page(page)
                        if text:
                            await tts_engine.play_page(page, text)
            elif action == "pause":
                if tts_engine:
                    tts_engine.pause()
            elif action == "resume":
                if tts_engine:
                    tts_engine.resume()
            elif action == "stop":
                if tts_engine:
                    tts_engine.stop()
            
            await broadcast_status()
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket错误: {e}")
        manager.disconnect(websocket)

if __name__ == "__main__":
    import socket
    try:
        # 获取局域网IP用于提示
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
        print(f" 手机/局域网: http://{lan_ip}:{settings.port}")
    except Exception:
        pass

    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
