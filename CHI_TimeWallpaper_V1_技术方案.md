# CHI TimeWallpaper V1 技术方案

版本：V1 原型版  
日期：2026-06-23  
项目目录：`/Users/lsn/Downloads/2027-chi-timewallpaper`

## 1. 产品定位

CHI TimeWallpaper V1 是一个面向亲情陪伴场景的动态壁纸原型。用户通过点击壁纸中的老人角色开始录音，再次点击结束录音；系统将语音转成文本，并根据文本生成新的当天壁纸。

V1 的目标不是完成完整商业化系统，而是验证一条最小闭环：

1. 手机壁纸式前端界面。
2. 点击人物触发录音。
3. 语音上传到后端。
4. 调用语音识别接口得到文本。
5. 调用生图接口生成新壁纸。
6. 新壁纸只替换“今天”页面，不影响“昨天”和“前天”页面。
7. 点击右上角进入聊天记录页面。

## 2. V1 功能范围

### 2.1 已实现功能

- 三个日期页面：前天、昨天、今天。
- 通过左右边缘透明热区切换页面。
- 右上角透明热区切换聊天记录和壁纸视图。
- 仅“今天”页面支持录音。
- 录音入口绑定在壁纸中老人所在区域，不再使用独立录音按钮。
- 手动开始和手动结束录音。
- 前端将录音编码为 WAV 文件上传。
- 后端调用音频转录接口。
- 后端根据转录文本调用图片生成接口。
- 生成的新图片保存到后端本地 `generated` 目录。
- 前端收到图片地址后替换“今天”页壁纸。
- 语音消息写入当天聊天记录。

### 2.2 暂不包含

- 用户登录、账号体系。
- 多设备云同步。
- 正式数据库持久化消息。
- 复杂人物抠图和动态识别。
- 多角色自动绑定。
- 支付、权限、内容审核后台。
- 生产级任务队列。
- 生产级对象存储 CDN。

## 3. 技术栈

### 3.1 前端

- Next.js 14
- React
- TypeScript
- Tailwind CSS
- Zustand
- Web Audio API

前端负责壁纸 UI、页面切换、聊天记录展示、录音采集、WAV 编码、请求后端接口和替换壁纸。

### 3.2 后端

- FastAPI
- Uvicorn
- httpx
- Pydantic Settings
- SQLAlchemy 预留

后端负责接收音频、调用外部语音识别接口、调用外部生图接口、保存生成图片、返回前端可访问的图片 URL。

### 3.3 外部模型服务

当前使用一个 OpenAI 兼容的 API 集成平台。

语音识别：

- 接口：`POST /v1/audio/transcriptions`
- 请求格式：`multipart/form-data`
- 文件字段：`file`
- 模型：`whisper-1`
- 语言：`zh`

图片生成：

- 接口：`POST /v1/chat/completions`
- 请求格式：`application/json`
- 模型示例：`gpt-4o-image`
- 输入：由后端构造的中文生图 prompt
- 输出：图片 URL、base64 或 markdown 图片引用，后端会解析并保存

## 4. 系统架构

```text
用户点击今天页老人区域
        |
        v
前端 Web Audio API 录音
        |
        v
前端编码 WAV Blob
        |
        v
POST /asr
        |
        v
FastAPI 后端
        |
        +--> 调用语音识别接口 /v1/audio/transcriptions
        |
        +--> 根据转录文本构造生图 prompt
        |
        +--> 调用生图接口 /v1/chat/completions
        |
        +--> 保存生成图片到 backend/storage/generated
        |
        v
返回 transcript + imageUrl
        |
        v
前端写入聊天记录，并替换今天页壁纸
```

## 5. 前端设计

### 5.1 页面结构

核心页面由三层组成：

- `WallpaperStage`：壁纸舞台容器。
- `AtmosphereLayer`：壁纸背景层。
- `ChatOverlay`：交互层、聊天层、日期切换层、录音热区。

主要文件：

- `frontend/app/page.tsx`
- `frontend/components/wallpaper/WallpaperStage.tsx`
- `frontend/components/wallpaper/AtmosphereLayer.tsx`
- `frontend/components/wallpaper/ChatOverlay.tsx`
- `frontend/lib/hooks/useRecorder.ts`
- `frontend/lib/hooks/useSceneStore.ts`
- `frontend/lib/api.ts`

### 5.2 日期页面

当前有三个页面：

- 前天
- 昨天
- 今天

页面状态由 `currentDayIndex` 控制。

切换方式：

- 点击左侧边缘透明区域：切换到前一天。
- 点击右侧边缘透明区域：切换到后一天。

之所以采用点击边缘，而不是滑动，是为了避免移动端或浏览器自带的手势冲突。

### 5.3 壁纸替换规则

V1 只允许生成图替换“今天”页面：

```ts
const wallpaper =
  currentDayIndex === 2 && generatedWallpaperUrl
    ? generatedWallpaperUrl
    : baseWallpaper;
```

这样可以保证：

- 前天页面保持历史状态。
- 昨天页面保持历史状态。
- 今天页面根据新语音生成新壁纸。

### 5.4 录音交互

录音不再使用独立按钮，而是绑定在今天页老人区域。

当前 V1 使用固定透明热区：

```tsx
className="absolute left-[8%] bottom-[8%] h-[44%] w-[42%]"
```

交互逻辑：

- 第一次点击老人区域：开始录音。
- 再次点击老人区域：结束录音。
- 结束后上传音频。
- 上传完成后进入识别和生图流程。

V1 采用固定区域绑定，适合快速验证。后续如果要适配每次生成图中老人位置变化，需要加入人物检测或抠图能力。

### 5.5 聊天记录

点击右上角透明区域进入聊天记录页面。

聊天记录按日期存储：

- 前天
- 昨天
- 今天

当前聊天记录保存在前端 Zustand 状态中，并写入 localStorage。

## 6. 后端设计

### 6.1 核心接口

#### `POST /asr`

用途：接收前端录音文件，完成语音识别和壁纸生成。

请求：

```http
POST /asr
Content-Type: multipart/form-data
```

字段：

```text
audio: WAV 音频文件
```

返回：

```json
{
  "transcript": "妈妈今天身体不太舒服",
  "imageUrl": "/generated/wallpaper-xxxx.png",
  "raw": {
    "asr": {},
    "image": {}
  }
}
```

### 6.2 语音识别流程

后端读取上传音频后，调用：

```text
POST {AUDIO_API_BASE_URL}/v1/audio/transcriptions
```

请求字段：

- `file`
- `model`
- `language`
- `response_format`
- `temperature`

当前默认配置：

```text
AUDIO_TRANSCRIPTION_MODEL=whisper-1
AUDIO_TRANSCRIPTION_LANGUAGE=zh
```

后端会从返回 JSON 中提取文本字段，兼容常见字段：

- `text`
- `utterance`
- `transcript`
- `sentence`
- `result`
- `results`
- `data`

### 6.3 图片生成流程

后端拿到转录文本后，构造中文生图 prompt，并调用：

```text
POST {IMAGE_API_BASE_URL}/v1/chat/completions
```

当前 prompt 的核心约束：

- 9:16 竖版手机壁纸。
- 不要文字、水印、UI、按钮、logo。
- 左下角一位老人。
- 右上角一位年轻人。
- 两个人形成明显对角线关系。
- 两个人不要近距离同框、不要拥抱、不要触摸。
- 中间用河流、山谷、小路、灯光、窗光等元素形成连接。
- 风格为温暖奇幻童话感数字插画。
- 上方保留干净空间，方便叠加锁屏时间。

后端会从生图接口返回中解析图片引用，支持：

- HTTP 图片 URL
- `data:image/...;base64,...`
- 纯 base64
- markdown 图片引用

解析成功后，图片会保存到：

```text
backend/storage/generated/
```

前端通过：

```text
/generated/filename.png
```

访问生成图片。

## 7. 环境变量

### 7.1 后端环境变量

后端运行时需要配置：

```bash
AUDIO_API_BASE_URL="https://你的平台API地址"
AUDIO_API_KEY="你的完整sk-key"
AUDIO_TRANSCRIPTION_MODEL="whisper-1"
AUDIO_TRANSCRIPTION_LANGUAGE="zh"
IMAGE_API_BASE_URL="https://你的平台API地址"
IMAGE_API_KEY="你的完整sk-key"
IMAGE_CHAT_MODEL="gpt-4o-image"
```

如果图片接口和语音接口使用同一个平台，可以不单独设置 `IMAGE_API_BASE_URL` 和 `IMAGE_API_KEY`，后端会复用音频配置。

### 7.2 前端环境变量

前端需要知道后端地址：

```bash
NEXT_PUBLIC_API_BASE="http://127.0.0.1:8000"
NEXT_PUBLIC_WS_BASE="ws://127.0.0.1:8000"
```

## 8. 本地运行命令

### 8.1 启动后端

```bash
cd /Users/lsn/Downloads/2027-chi-timewallpaper/backend

AUDIO_API_BASE_URL="https://你的平台API地址" \
AUDIO_API_KEY="你的完整sk-key" \
AUDIO_TRANSCRIPTION_MODEL="whisper-1" \
AUDIO_TRANSCRIPTION_LANGUAGE="zh" \
IMAGE_CHAT_MODEL="gpt-4o-image" \
.venv-mac/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

如果你的图片接口和语音接口不是同一个 base url，可以使用：

```bash
cd /Users/lsn/Downloads/2027-chi-timewallpaper/backend

AUDIO_API_BASE_URL="https://你的语音API地址" \
AUDIO_API_KEY="你的语音sk-key" \
AUDIO_TRANSCRIPTION_MODEL="whisper-1" \
AUDIO_TRANSCRIPTION_LANGUAGE="zh" \
IMAGE_API_BASE_URL="https://你的生图API地址" \
IMAGE_API_KEY="你的生图sk-key" \
IMAGE_CHAT_MODEL="gpt-4o-image" \
.venv-mac/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 8.2 启动前端

```bash
cd /Users/lsn/Downloads/2027-chi-timewallpaper/frontend

NEXT_PUBLIC_API_BASE="http://127.0.0.1:8000" \
NEXT_PUBLIC_WS_BASE="ws://127.0.0.1:8000" \
npm run dev
```

浏览器打开：

```text
http://localhost:3000
```

## 9. 当前 V1 数据流

### 9.1 录音到聊天记录

1. 用户点击今天页老人区域。
2. 前端请求麦克风权限。
3. Web Audio API 采集音频。
4. 用户再次点击老人区域。
5. 前端停止录音并编码 WAV。
6. 前端上传到 `/asr`。
7. 后端返回转录文本。
8. 前端将文本写入今天页聊天记录。

### 9.2 转录到壁纸生成

1. 后端拿到转录文本。
2. 后端构造生图 prompt。
3. 后端调用生图接口。
4. 后端解析返回图片。
5. 后端保存图片到本地 generated 目录。
6. 后端返回图片 URL。
7. 前端替换今天页壁纸。

## 10. 当前限制和风险

### 10.1 录音热区是固定位置

当前点击老人触发录音，依赖固定的透明区域。只要生成图中老人仍然在左下角，这个方案就可以工作。

风险是：如果生图结果中老人位置偏移，用户点击老人时可能不在热区内。

V1 处理方式：

- 在 prompt 中强约束老人位于左下角。
- 前端热区覆盖左下方较大区域。

### 10.2 `/asr` 同时负责识别和生图

当前 `/asr` 接口会串行执行语音识别和图片生成。

优点：

- 前端调用简单。
- V1 闭环最短。

风险：

- 生图慢时，接口响应时间会变长。
- 如果生图失败，可能影响整次请求。

后续优化方向：

- `/asr` 只返回转录文本。
- `/generate-wallpaper` 单独异步生成。
- 前端先展示聊天记录，再等待壁纸更新。

### 10.3 本地图片存储

当前生成图片保存在本地文件夹，适合原型。

生产环境需要替换为：

- 对象存储
- CDN
- 图片生命周期管理
- 用户级访问控制

### 10.4 外部 API 返回格式不稳定

当前生图接口是 OpenAI chat 兼容格式，但不同平台返回图片的字段可能不同。

V1 已做兼容解析，但如果平台返回结构变化，仍可能需要补充解析规则。

## 11. V1 到 V2 的建议

V2 可以优先做以下几项：

1. 拆分 `/asr` 和 `/generate-wallpaper`，避免生图失败影响语音识别。
2. 将生成任务改为异步，前端显示生成中状态。
3. 接入人物检测或分割模型，自动识别老人位置并生成点击热区。
4. 将聊天记录和生成图保存到后端数据库。
5. 接入对象存储保存生成图片。
6. 增加图片生成失败后的重试和 fallback prompt。
7. 增加移动端真机触摸测试，优化 iPhone 和 iPad 的点击区域。

## 12. 当前结论

CHI TimeWallpaper V1 已经可以完成核心验证：

- 用户在壁纸中点击老人。
- 说一段语音。
- 系统识别语音。
- 系统根据语音生成当天新壁纸。
- 新壁纸只影响今天，不改变昨天和前天。
- 右上角可以进入聊天记录查看当天语音内容。

这个版本适合作为产品演示原型，也适合继续迭代人物识别、异步生图和移动端部署。
