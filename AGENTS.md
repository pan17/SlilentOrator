**基本原则**
1. 行动前思考 — 不要假设，分析问题相关的所有内容，明确说明假设
2. 简洁优先 — 用最少的步骤解决问题
3. 精准修改 — 只碰必须碰的，只清理自己造成的混乱
4. 目标驱动执行 — 定义成功标准，循环验证直到达成
5. 文档同步 — 每次修改代码或结构后，检查 AGENTS.md 和 README.md 是否需要同步更新，确保文档反映真实项目状态

---

## 项目概览

Windows 后台服务 + 浏览器遥控端。核心功能：PPT 翻页控制 + 双 TTS 引擎（edge-tts / Omnivoice 语音克隆）。

**真实目录结构（不是README里的）：**
```
server/
  main.py           # FastAPI 入口，启动 uvicorn
  ppt_controller.py # pywin32 COM 控制 PowerPoint
  tts_engine.py     # TTS 引擎（edge-tts + Omnivoice 语音克隆）
  script_parser.py  # 解析 md 演讲稿（支持中文数字页码）
  config.py         # pydantic-settings 配置
  static/index.html # 移动端优先的网页遥控界面
  requirements.txt
pptresource/         # 演讲项目目录（每个子文件夹一个项目）
  sample/
    script.md       # 演讲稿
    tts_cache/      # 按页缓存 page_001.mp3...
start.bat          # Windows 一键启动（含 venv 创建）
venv/              # 由 start.bat 自动创建的虚拟环境
```

---

## 环境要求

- Windows 10/11 + PowerPoint 已安装
- Python（通过 start.bat 自动创建 venv，无需手动安装依赖）
- VLC Media Player（[下载地址](https://www.videolan.org/vlc/)，TTS 音频播放依赖）
- Omnivoice 引擎需额外安装：`pip install gradio-client` 并运行本地部署的 Omnivoice 服务

---

## 启动方式

```cmd
# 方式1：一键启动（推荐）
.\start.bat

# 方式2：手动启动
python server/main.py
```

启动后访问：
- 网页控制台：`http://localhost:8000`（同一局域网设备用 `http://服务端IP:8000`）
- API 文档：`http://localhost:8000/docs`

---

## 已知环境问题

**TTS DNS 失败（aiodns/pycares）**

在部分 Windows + Python 3.14 环境下，aiohttp 优先使用 aiodns（基于 c-ares）做异步 DNS 解析，但 c-ares 初始化失败。现象：
- `edge-tts` CLI 报错：`Cannot connect to host speech.platform.bing.com:443 [Could not contact DNS servers]`
- 系统 `nslookup` / `ping` 正常（走 Windows DNS）

修复：
```cmd
pip uninstall aiodns pycares -y
```
卸载后 aiohttp 回退到系统 DNS（ThreadedResolver），edge-tts 正常工作。

---

## 关键实现细节

**演讲稿格式**（`server/script_parser.py`）：
```markdown
第一页：
内容...

第二页：
内容...
```
同时支持 `第1页：`和 `第一页：`格式。

**WebSocket**（`/ws`）：
- 连接后立即推送当前状态 `{type: "status_update", data: {...}}`
- 客户端发送 `{action: "next"|"prev"|"goto"|"play"|"pause"|"resume"|"stop", page?: number}`
- 服务端在 PPT 操作、TTS 状态变化、预生成进度变化时主动广播

**演讲稿项目管理**（`pptresource/`）：
- 每个子文件夹为一个演讲项目，文件夹名即项目名
- 项目结构：`项目名/script.md`（演讲稿）+ `项目名/*.pptx`（PPT）+ `项目名/tts_cache/`（音频缓存）
- 网页控制台可列出所有项目、选择加载，自动找PPT并打开 + 加载演讲稿

**TTS 引擎**（`server/tts_engine.py`）：
- 双引擎架构：`TTSEngine`（edge-tts）+ `OmnivoiceEngine`（本地语音克隆）
- 网页端可运行时热切换引擎（`GET/POST /api/tts/engine`）
- 切换引擎时自动保留缓存目录

**Omnivoice 引擎**（`OmnivoiceEngine`）：
- 通过 `gradio_client` 调用本地部署的 Omnivoice API（默认 http://localhost:8001）
- 参数包括：`ref_aud`（参考音频路径）、`ref_text`、`lang`、`instruct`、`ns`、`gs`、`sp` 等
- `du` 固定传 0，由 Omnivoice 根据 speed 自动计算时长
- 参考音频路径会自动清洗不可见 Unicode 控制字符
- 输出 WAV 格式，自动识别为有效缓存（文件头检测支持 MP3 + WAV）

**TTS 音频缓存**：
- 按页缓存：文件名 `page_001.mp3`，一页对应一个缓存文件
- 缓存在各项目目录下的 `tts_cache/`，按项目隔离
- 支持检查缓存有效性（文件大小 + 文件头校验：MP3 ID3/0xFF、WAV RIFF）
- 网页端可显式触发「预生成音频」，逐页生成，已有且有效的跳过
- 播放时优先使用缓存，纯本地播放零延迟；无缓存时回退到即时生成

---

## 不要做的事

- 不要修改 `venv/` 目录下的任何文件（由 start.bat 管理）