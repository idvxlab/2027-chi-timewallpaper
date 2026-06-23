# 2027-chi-timewallpaper

一个面向儿童陪伴场景的"时间壁纸"应用:环境氛围层 + 互动热点 + 语音/触摸驱动场景切换。

> V1 状态:仅做"可运行性修复 + 联调",不接真实 Provider(豆包 / Whisper / bltcy)。所有外部依赖均走 mock。

## V1 场景状态(四选一)

| demoId          | 含义       | 光晕 | 环境音             |
| --------------- | ---------- | ---- | ------------------ |
| `calm`          | 平静(默认) | 关   | -                  |
| `longing`       | 想念       | 开   | `/audio/ambient.mp3` |
| `fatigue`       | 疲倦       | 开   | `/audio/ambient.mp3` |
| `await_response`| 等待回应   | 开   | `/audio/ambient.mp3` |

## 目录结构

```
.
├── frontend/   Next.js + React + TS 前端
├── backend/    FastAPI 后端,负责场景编排、ASR/LLM/图像 Provider 接入(默认 mock)
├── docker-compose.yml
└── .env.example
```

## 快速开始(Docker)

```bash
cp .env.example .env
docker compose up --build
```

- 前端: http://localhost:3000
- 后端: http://localhost:8000/docs

## 本地开发(推荐用于 V1 联调)

### 1. 准备素材(本地必须手动放入,仓库不含)

把以下文件分别放到对应路径,**文件名必须严格一致**:

**壁纸图片**(放到 `frontend/public/wallpaper/`)
```
frontend/public/wallpaper/neutral-base.jpg     # 默认主背景(calm/longing/fatigue 都用)
frontend/public/wallpaper/window-glow.png      # 思念/疲倦/等待回应时的光晕
frontend/public/wallpaper/cloud-overlay.png     # 云朵叠层
frontend/public/wallpaper/vignette.png         # 暗角
```

**音频**(放到 `frontend/public/audio/`)
```
frontend/public/audio/ambient.mp3              # 三个非平静状态的循环环境音
frontend/public/audio/parent-msg-01.mp3         # 触碰小夜灯/绘本 时的父母语音提示
frontend/public/audio/child-msg-01.mp3          # 触碰小熊 时的孩子语音提示
```

> 缺素材时:页面能正常打开(图片位置透明黑底,音频静默),不会崩。**但 demo 切换会没有视觉/听觉反馈。**

### 2. 启动后端

> Python 3.11+ 建议。后端**不会**自动 mount 前端 `public/audio`,音频由前端静态服务提供。

```bash
cd backend
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Windows cmd:
.\.venv\Scripts\activate.bat
# macOS / Linux:
# source .venv/bin/activate

pip install -r requirements.txt

# 第一次跑:在 backend/ 下复制一份环境变量
# (默认值已经够 V1 联调,不复制也能跑)
# copy .env.example ..\..\.env  (Windows,可选)

uvicorn app.main:app --reload --port 8000
```

- 健康检查: http://localhost:8000/health
- OpenAPI 文档: http://localhost:8000/docs
- WebSocket 场景推送: `ws://localhost:8000/ws/scene`

后端首次启动会自动创建 SQLite `backend/data/app.db`,用于触碰/ASR 日志。

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

- 打开 http://localhost:3000
- 顶部的 4 个按钮(`平静 / 想念 / 疲倦 / 等待回应`)点击后,会向 `POST /demo/<id>` 切场景。
- 中间的 5 个圆点是 hotspots,点击会发 `POST /touch { hotspotId }`,后端返回 `toast` 文案 + 短提示音 cue。

### 4. 跑测试

```bash
cd backend
.\.venv\Scripts\Activate.ps1
pytest -q
```

测试覆盖:
- `/health`
- `/scene` 默认是 `calm`
- 4 种 demo 状态切换
- 未知 demo 返回 404
- 触碰(用驼峰 `hotspotId`)返回 toast + scene

## Provider 抽象(V1 默认 mock)

| Provider        | 默认 | 切换方式                       |
| --------------- | ---- | ------------------------------ |
| `asr_provider`  | `mock` | 设置 `ASR_PROVIDER=whisper` + `WHISPER_API_KEY=...` |
| `llm_provider`  | `mock` | (未实现)                       |
| `image_provider`| `mock` | (未实现)                       |
| `storage_provider` | `local` | (未实现)                    |

**Whisper/doubao/bltcy 真实接入**不在 V1 范围。`requirements.txt` 没有 `openai-whisper` 之类的大模型依赖,安装 / 启动很快。

## 已知 V1 限制

- 触碰日志使用 SQLAlchemy 2.0 的 `Session.query()`(deprecated 但仍可用),不影响功能。
- audio 的 `playCue` 走前端 HTML5 Audio,无 fallback 到 SoundJS / Howler。
- WebSocket 没有心跳 / 重连退避,断线后由下一次 `connect()` 重连。
- `useSceneSocket` 是 zustand store,主要用于全局 scene + toast 状态(可保留,后续接录音 / 上传逻辑方便)。
