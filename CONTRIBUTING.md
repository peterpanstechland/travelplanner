# 贡献指南

感谢您考虑为旅行规划助手项目做出贡献！这个指南将帮助您了解如何参与项目开发。

## 开发环境设置

1. Fork并克隆项目
```bash
git clone https://github.com/YOUR-USERNAME/travelplanner.git
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

4. 设置环境变量
从`.env.example`复制创建`.env`文件，并填入您的API密钥。

## 贡献流程

1. 创建功能分支
```bash
git checkout -b feature/your-feature-name
```

2. 编码和测试
   - 遵循项目的代码风格和约定
   - 为新功能添加适当的测试
   - 确保所有测试通过

3. 提交更改
   - 使用语义化提交消息
   - 可以参考项目根目录的`.gitmessage`模板
```bash
git add .
git commit
```

4. 推送到您的仓库
```bash
git push origin feature/your-feature-name
```

5. 创建Pull Request
   - 从您的Fork向主仓库提交Pull Request
   - 清晰地描述您的更改及其目的

## 代码规范

- 使用[PEP 8](https://www.python.org/dev/peps/pep-0008/)风格指南
- 添加适当的文档字符串
- 使用有意义的变量和函数命名

## 版本管理

项目使用[语义化版本控制](https://semver.org/)。版本号格式为：`主版本.次版本.修订号`。

- 主版本：不兼容的API变更
- 次版本：向后兼容的功能添加
- 修订号：向后兼容的问题修复

## 提交Pull Request前的检查清单

- [ ] 代码遵循项目风格指南
- [ ] 添加/更新了相关测试
- [ ] 所有测试都通过
- [ ] 更新了相关文档
- [ ] 更新了CHANGELOG（如果适用）

## 许可

通过提交代码，您同意您的贡献将在MIT许可下提供。 