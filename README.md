# 旅行规划助手 (Travel Planner)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
![Version](https://img.shields.io/badge/version-1.0.0-blue)

基于高德地图API和Claude LLM的智能旅行规划助手，提供路线规划、天气查询、景点搜索等功能，通过Web界面进行交互。

## 功能特点

- 🗺️ **多种交通方式规划**：支持驾车、步行、公共交通和骑行路线规划
- 🌦️ **天气信息查询**：获取目的地天气情况，规划更合理的出行时间
- 🏞️ **景点搜索推荐**：查找目的地周边景点，支持评分和口碑筛选
- 💬 **智能对话体验**：自然语言交互，支持多轮对话复杂行程规划
- 🧠 **上下文记忆功能**：记住旅行偏好和计划，无需重复输入信息
- 🌐 **Web界面交互**：直观易用的界面，支持移动端和桌面端
- 📊 **实时处理状态**：查询处理进度实时显示，响应更加透明

## 快速开始

### 依赖安装

1. 克隆项目
```bash
git clone https://github.com/yourusername/travelplanner.git
cd travelplanner
```

2. 创建并激活conda环境
```bash
conda create -n travelplanner python=3.9
conda activate travelplanner
```

3. 安装依赖
```bash
pip install -r requirements.txt
```

### 配置API密钥

您需要创建一个`.env`文件（已在.gitignore中排除）来存储API密钥。文件包含以下内容：

```
CLAUDE_API_KEY=你的Claude API密钥
MCP_SERVER_URL=https://mcp.amap.com/sse
MCP_PROJECT_KEY=你的高德地图MCP项目密钥
```

#### 获取API密钥的方法：

1. **Claude API密钥**：
   - 访问 [Anthropic Console](https://console.anthropic.com/)
   - 注册或登录账户
   - 导航至"API Keys"并创建新密钥

2. **高德地图MCP密钥**：
   - 访问 [高德开放平台](https://lbs.amap.com/)
   - 注册或登录账户
   - 创建应用并获取密钥

### 运行应用

1. 确保已激活conda环境：
```bash
conda activate travelplanner
```

2. 启动Web服务器（会自动启动MCP服务器）：
```bash
python web_server.py
```

3. 在浏览器中访问：
```
http://localhost:8000
```

## 架构说明

本项目采用前后端分离的架构设计：

- **前端**：HTML/CSS/JavaScript，提供用户交互界面
- **后端**：FastAPI (Python)，处理请求并管理WebSocket连接
- **MCP服务**：提供高德地图API的封装，基于Model Context Protocol
- **LLM集成**：使用Claude API处理自然语言请求并生成旅行建议

```
┌─────────────┐       ┌─────────────┐       ┌────────────────┐
│   Browser   │◄─────►│  web_server │◄─────►│ amap_mcp_server│
│  (HTML/JS)  │       │  (FastAPI)  │       │     (MCP)      │
└─────────────┘       └─────────────┘       └────────────────┘
                            ▲                       ▲
                            │                       │
                            ▼                       ▼
                      ┌─────────────┐       ┌─────────────┐
                      │  client.py  │       │  高德地图API │
                      │  (Claude)   │       │             │
                      └─────────────┘       └─────────────┘
```

## 文件结构

- `web_server.py`: FastAPI服务器，处理HTTP请求和WebSocket连接
- `amap_mcp_server.py`: MCP服务器实现，封装高德地图API
- `client.py`: Claude客户端，处理自然语言处理和查询生成
- `static/`: 前端文件
  - `index.html`: Web界面HTML
  - `app.js`: 前端JavaScript逻辑
  - `styles.css`: 样式表

## 版本历史

### v1.0.0 (2024-04-06)
- 🚀 初始版本发布
- ✨ 支持路线规划、天气查询、景点搜索
- 🌐 实现Web界面交互
- 🧩 增强错误处理和稳定性
- 🔄 实现WebSocket实时状态更新

## 贡献指南

欢迎贡献代码、报告问题或提出改进建议。请先fork项目，然后提交pull request。

## 许可证

本项目采用MIT许可证 - 详见 [LICENSE](LICENSE) 文件。 