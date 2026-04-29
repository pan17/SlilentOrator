# SlilentOrator - PPT嘴替

Windows后台服务 + 安卓遥控端，实现PPT远程翻页与TTS语音播放。

## 项目结构

```
SlilentOrator/
├── server/              # Windows服务端
│   ├── main.py          # FastAPI入口
│   ├── ppt_controller.py # PPT控制模块
│   ├── tts_engine.py    # TTS引擎（edge-tts）
│   ├── script_parser.py # 演讲稿解析器
│   ├── config.py        # 配置文件
│   ├── requirements.txt # Python依赖
│   └── static/          # 网页遥控端（移动端优先）
├── scripts/             # 演讲稿目录
│   └── demo.md          # 示例演讲稿
├── start.bat            # Windows一键启动脚本
└── README.md
```

## 快速开始

### 1. 环境要求

- Windows 10/11
- Python 3.9+
- Microsoft PowerPoint（已安装）
- VLC Media Player（TTS播放依赖，[下载地址](https://www.videolan.org/vlc/)）

### 2. 启动服务

双击 `start.bat` 或在命令行执行：

```bash
# 手动启动
pip install -r server/requirements.txt
python server/main.py
```

服务启动后：
- API地址：`http://localhost:8000`
- API文档：`http://localhost:8000/docs`

### 3. 演讲稿格式

在 `scripts/` 目录下创建 `.md` 文件，格式如下：

```markdown
第一页：
这是第一页的演讲内容。

第二页：
这是第二页的演讲内容。

第三页：
这是第三页的演讲内容。
```

## API接口

### 状态查询
- `GET /api/status` - 获取完整状态（PPT页码、TTS状态、演讲稿信息）

### PPT控制
- `POST /api/ppt/next` - 下一页
- `POST /api/ppt/prev` - 上一页
- `POST /api/ppt/goto/{page}` - 跳转到指定页
- `POST /api/ppt/start` - 开始放映
- `POST /api/ppt/stop` - 结束放映

### TTS控制
- `POST /api/tts/play?page=1` - 播放指定页演讲稿
- `POST /api/tts/pause` - 暂停播放
- `POST /api/tts/resume` - 继续播放
- `POST /api/tts/stop` - 停止播放

### 演讲稿管理
- `POST /api/script/load?file_name=demo.md` - 加载演讲稿
- `GET /api/script/pages` - 获取所有页面内容
- `GET /api/script/page/{page}` - 获取指定页内容

### WebSocket
- `WS /ws` - 实时状态推送，支持双向控制

WebSocket消息格式：
```json
{
  "action": "next" | "prev" | "goto" | "play" | "pause" | "resume" | "stop",
  "page": 1  // goto/play时指定页码
}
```

## 后续计划

- [ ] 语音克隆替换edge-tts
- [ ] PPT备注页自动读取演讲稿
- [ ] 激光笔功能
- [ ] 演讲计时器
