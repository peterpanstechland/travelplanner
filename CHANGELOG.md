# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2024-04-06

### Added
- 初始版本发布
- Web界面交互功能
- 实时状态更新通过WebSocket实现
- 多种交通方式规划（驾车、步行、公共交通和骑行）
- 天气信息查询功能
- 景点搜索推荐功能
- 智能对话体验，基于Claude LLM
- 上下文记忆功能

### Fixed
- WebSocket连接稳定性问题
- 查询响应匹配问题，确保返回内容与查询相关
- 服务器初始化错误处理

### Changed
- 优化API调用频率
- 改进错误提示和降级体验
- 更稳健的服务器启动和关闭流程 