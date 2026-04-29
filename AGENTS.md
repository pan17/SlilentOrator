**四项基本原则**
1. 行动前思考 — 不要假设，分析问题相关的所有内容，明确说明假设
2. 简洁优先 — 用最少的步骤解决问题
3. 精准修改 — 只碰必须碰的，只清理自己造成的混乱
4. 目标驱动执行 — 定义成功标准，循环验证直到达成

---

## 项目概览

Windows 后台服务 + 浏览器遥控端。核心功能：PPT 翻页控制 + edge-tts 语音播放。

**真实目录结构（不是README里的）：**
```
server/
  main.py           # FastAPI 入口，启动 uvicorn
  ppt_controller.py # pywin32 COM 控制 PowerPoint
  tts_engine.py     # edge-tts 生成音频 + VLC 播放
  script_parser.py  # 解析 md 演讲稿（支持中文数字页码）
  config.py         # pydantic-settings 配置
  static/index.html # 移动端优先的网页遥控界面
  requirements.txt
scripts/demo.md    # 示例演讲稿
start.bat          # Windows 一键启动（含 venv 创建）
venv/              # 由 start.bat 自动创建的虚拟环境
```

---

## 环境要求

- Windows 10/11 + PowerPoint 已安装
- Python（通过 start.bat 自动创建 venv，无需手动安装依赖）
- VLC Media Player（[下载地址](https://www.videolan.org/vlc/)，TTS 音频播放依赖）

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
- 服务端在 PPT 操作、TTS 状态变化时主动广播

**TTS 音频缓存**（`server/tts_engine.py`）：
- 文本 MD5 前12位命名缓存文件，重复播放直接复用
- 缓存在 `temp/silent_orator_tts/` 目录

---

## 不要做的事

- 不要修改 `venv/` 目录下的任何文件（由 start.bat 管理）