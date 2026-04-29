import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
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
_main_loop: Optional[asyncio.AbstractEventLoop] = None  # 主线程event loop，供后台线程调用

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

def get_full_status() -> dict:
    """获取完整状态"""
    status = {
        "ppt": ppt_controller.get_status() if ppt_controller else {"error": "未初始化"},
        "tts": tts_engine.get_status() if tts_engine else {"error": "未初始化"},
        "script": {
            "total_pages": script_parser.get_total_pages() if script_parser else 0,
            "loaded": script_parser is not None
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
    
    # 初始化演讲稿解析器（尝试加载默认文件）
    try:
        default_script = settings.scripts_dir / "demo.md"
        if default_script.exists():
            script_parser = ScriptParser.from_file(default_script)
            logger.info(f"演讲稿加载成功: {default_script}, 共 {script_parser.get_total_pages()} 页")
    except Exception as e:
        logger.warning(f"默认演讲稿加载失败: {e}")
    
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

# ==================== TTS控制接口 ====================

@app.post("/api/tts/play")
async def tts_play(page: Optional[int] = None):
    """播放指定页或当前页的演讲稿"""
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
    
    success = await tts_engine.play(text)
    await broadcast_status()
    
    return {"success": success, "page": page}

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

@app.post("/api/script/load")
async def load_script(file_name: str):
    """加载演讲稿文件"""
    global script_parser
    
    file_path = settings.scripts_dir / file_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_name}")
    
    try:
        script_parser = ScriptParser.from_file(file_path)
        await broadcast_status()
        return {
            "success": True,
            "total_pages": script_parser.get_total_pages(),
            "file": file_name
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"加载失败: {str(e)}")

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
                            await tts_engine.play(text)
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
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
