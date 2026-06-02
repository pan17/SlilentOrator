# SlilentOrator - PPT 嘴替

PPT 翻页 + AI 语音朗读，浏览器当遥控器。

手机打开网页就能控制电脑上的 PPT 翻页和语音播放，演讲时不用再跑回电脑前。

![screenshot](https://img.shields.io/badge/platform-Windows-blue) ![license](https://img.shields.io/badge/license-MIT-green)

## 30 秒上手

### 1. 双击 `start.bat`

自动安装依赖、启动服务，打开浏览器访问：

```
http://localhost:8000        ← 电脑本机
http://服务端IP:8000         ← 手机/其他设备（同一局域网）
```

### 2. 准备演讲项目

在 `pptresource/` 下建一个文件夹，放入：

| 文件 | 说明 |
|------|------|
| `*.pptx` | PPT 文件 |
| `script.md` | 演讲稿（按页分段，见下方格式） |

示例 `pptresource/sample/script.md`：
```markdown
第一页：
大家好，今天分享的主题是...

第二页：
首先来看第一部分...
```

### 3. 网页端操作

1. **加载项目** — 选择刚才建的文件夹，点击「加载」
2. **翻页** — 点击「上一页」「下一页」或页面小圆点
3. **播放语音** — 点击「播放」按钮，自动朗读当前页演讲稿
4. **预生成** — 提前缓存所有页面的语音，播放时零延迟

---

## 环境要求

- Windows 10/11
- Python 3.9+（`start.bat` 自动创建虚拟环境）
- Microsoft PowerPoint
- VLC Media Player（`start.bat` 自动检测，未安装时尝试自动安装）

---

## 演讲稿格式

```markdown
第一页：
内容...

第二页：
内容...

第三页：
内容...
```

支持 `第一页：`、`第1页：`、`页1：` 等格式。

---

## TTS 引擎

内置两个引擎，网页端可运行时热切换：

| 引擎 | 说明 | 依赖 |
|------|------|------|
| **edge-tts** | 微软云端 TTS，开箱即用 | 网络 |
| **Omnivoice** | 本地语音克隆，音质更好 | gradio-client + 本地部署的 Omnivoice 服务 |

### Omnivoice 配置

1. 本地部署 Omnivoice 服务（默认端口 8001）
2. 在项目根目录 `ref/` 下放置：
   - 参考音频（`.wav` / `.mp3`）— 你要克隆的声音样本
   - 参考文本（`.txt`）— 音频对应的文字
3. `start.bat` 会自动读取 `ref/` 下的文件作为默认值
4. 也可通过网页端「TTS 引擎设置」面板手动配置

---

## 项目结构

```
SlilentOrator/
├── server/              # 服务端
│   ├── main.py          # FastAPI 入口
│   ├── ppt_controller.py # PowerPoint 控制
│   ├── tts_engine.py    # TTS 引擎（edge-tts + Omnivoice）
│   ├── script_parser.py # 演讲稿解析
│   ├── config.py        # 配置（可通过 .env 覆盖）
│   ├── requirements.txt
│   └── static/index.html # 网页遥控端
├── pptresource/         # 演讲项目目录（文件被 gitignore）
├── ref/                 # Omnivoice 参考音频/文本（文件被 gitignore）
├── start.bat            # 一键启动
├── LICENSE              # MIT License
└── README.md
```

---

## 配置

通过 `.env` 文件或环境变量覆盖默认值：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `tts_engine` | 引擎选择 | `edge-tts` |
| `omnivoice_url` | Omnivoice 服务地址 | `http://192.100.200.73:8001` |
| `omnivoice_ref_audio` | 参考音频路径 | 自动读取 `ref/` 目录 |
| `omnivoice_ref_text` | 参考文本 | 自动读取 `ref/` 目录 |
| `omnivoice_sp` | 语速 | `1.0` |
| `tts_voice` | edge-tts 语音 | `zh-CN-XiaoxiaoNeural` |

---

## API 接口

### 状态

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 完整状态 |

### 项目管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/projects` | 列出所有项目 |
| POST | `/api/projects/load?name=xxx` | 加载项目（打开 PPT + 加载演讲稿） |

### PPT 控制

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ppt/next` | 下一页 |
| POST | `/api/ppt/prev` | 上一页 |
| POST | `/api/ppt/goto/{page}` | 跳转到指定页 |
| POST | `/api/ppt/start` | 开始放映 |
| POST | `/api/ppt/stop` | 结束放映 |

### TTS 控制

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tts/play?page=1` | 播放指定页 |
| POST | `/api/tts/pause` | 暂停 |
| POST | `/api/tts/resume` | 继续 |
| POST | `/api/tts/stop` | 停止 |
| GET | `/api/tts/cache` | 获取缓存状态 |
| POST | `/api/tts/pre_generate` | 预生成所有页面音频 |
| POST | `/api/tts/delete_cache` | 删除缓存（`?page=3` 删指定页） |

### TTS 引擎

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tts/engine` | 获取引擎配置 |
| POST | `/api/tts/engine` | 运行时热切换引擎 |

### WebSocket

```
WS /ws
```

双向通信，支持 `next` / `prev` / `goto` / `play` / `pause` / `resume` / `stop`。

---

## 后续计划

- [ ] PPT 备注页自动读取演讲稿
- [ ] 激光笔功能
- [ ] 演讲计时器
