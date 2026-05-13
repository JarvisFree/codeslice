# 🔪 CodeSlice

> 用自然语言提问，AI 帮你从大型代码仓库中精准切出相关文件。

CodeSlice 是一个运行在本地的代码分析工具。你只需输入仓库路径和一句问题，它会通过 DeepSeek AI 进行两阶段分析，自动定位与问题最相关的源文件，并将结果打包成 ZIP 供你下载——方便直接投喂给 AI 助手或做 Code Review。

---

## ✨ 功能特性

- **两阶段 AI 筛选**：第一阶段根据文件树快速筛候选，第二阶段读取文件内容精确过滤，大幅降低噪音
- **Import 引用链追踪**（可选）：自动向上追溯所有 `import` / `require` 依赖，确保上下文完整
- **截图辅助分析**：支持上传页面截图，结合视觉信息提升定位准确度
- **实时进度推送**：基于 SSE（Server-Sent Events）的流式进度反馈，分析过程可见
- **一键打包下载**：将最终筛选结果打包为同名 ZIP，也支持在结果页手动调整文件列表后重新下载
- **灵活的 API Key 配置**：支持环境变量注入，也可在界面实时填写，无需重启服务

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

依赖清单：

```
flask>=3.0.0
openai>=1.0.0
```

### 2. 配置 API Key

**方式 A：环境变量（推荐，一次配置永久生效）**

```bash
export CODESLICE_DEEPSEEK_API_KEY="your-deepseek-api-key"
```

Windows：

```bat
set CODESLICE_DEEPSEEK_API_KEY=your-deepseek-api-key
```

**方式 B：界面填写**

直接在 Web 界面的 API Key 输入框中填写，每次会话有效。

### 3. 启动服务

```bash
python app.py
```

启动后访问 [http://localhost:5000](http://localhost:5000)

---

## 🔧 使用方式

1. **填写仓库路径**：输入本地代码仓库的绝对路径，例如 `/Users/yourname/projects/my-app`
2. **描述你的问题**：用自然语言说明你关心的功能或 Bug，例如「用户登录流程在哪里处理的？」
3. **可选配置**：
   - 上传截图（Bug 截图 / 设计稿），辅助 AI 理解上下文
   - 勾选「追踪 Import 引用链」以包含所有依赖文件
   - 选择分析模型（默认 `deepseek-v4-pro`）
4. **开始分析**：点击分析按钮，实时查看 AI 分析进度
5. **下载结果**：分析完成后，点击下载按钮获取包含相关文件的 ZIP 压缩包

---

## ⚙️ 分析流程

```
仓库路径
   │
   ▼
[扫描文件树]  ──────────────────────────────── 忽略 node_modules / dist / .git 等
   │
   ▼
[第一阶段] AI 分析文件树，筛出 20~50 个候选文件
   │
   ▼
[第二阶段] 读取候选文件内容，AI 精确过滤出直接相关文件
   │
   ▼（可选）
[第三阶段] 静态分析 import/require，补全所有依赖文件
   │
   ▼
打包下载 ZIP
```

**文件过滤规则：**
- 自动忽略目录：`node_modules`、`dist`、`build`、`.git`、`__pycache__`、`vendor` 等
- 自动忽略扩展名：编译产物、图片/音视频、字体、压缩包、二进制文档等
- 单文件大小上限：150 KB（超出部分截断）
- 单次分析内容上限：约 40 万字符（~100K tokens）

---

## 📁 项目结构

```
codeslice/
├── app.py              # Flask 服务，路由与 SSE 进度推送
├── analyzer.py         # 核心分析逻辑（文件扫描、两阶段 AI 筛选、import 追踪）
├── templates/
│   └── index.html      # 前端界面
└── requirements.txt    # Python 依赖
```

---

## 🔌 API 说明

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | Web 界面 |
| `GET` | `/config` | 返回是否已配置环境变量 API Key |
| `POST` | `/analyze` | 启动分析任务，返回 `job_id` |
| `GET` | `/progress/:job_id` | SSE 流，实时推送分析进度 |
| `GET` | `/download/:dl_id` | 下载分析结果 ZIP |
| `POST` | `/redownload` | 按自定义文件列表重新打包下载 |

---

## 🛠️ 环境要求

- Python 3.10+
- DeepSeek API Key（[获取地址](https://platform.deepseek.com/)）

---

## 📄 License

MIT
