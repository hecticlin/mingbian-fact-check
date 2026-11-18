# 明辨 图文新闻辅助核查系统

明辨是一个中文新闻辅助核查网页。纯文本路径将输入拆成主干事件、次要事件和隐含信息，使用 DeepSeek 进行初判与复核。图文路径使用通义视觉模型分别分析文本、图像和图文一致性。网页另外提供检测历史、争议结果提交复审和辟谣资讯。检测结果供人工核验参考。

参赛技术说明记录了内置提示词、启用位置、可验证环节和材料缺口。[查看技术说明 PDF](docs/mingbian-technical-note.pdf)

## 源码入口

| 环节 | 源码 |
| --- | --- |
| 网页入口与 Flask Blueprint | [`app.py`](app.py) |
| 文本检测 SSE 路由 | [`routes/llm_detect.py`](routes/llm_detect.py) |
| 文本抽取及判断模板 | [`fake_news_detector/implicit_types.py`](fake_news_detector/implicit_types.py) |
| 快速信源核查与两阶段复核 | [`authority_check.py`](fake_news_detector/authority_check.py)、[`dual_model.py`](fake_news_detector/dual_model.py) |
| 图文核查提示词、执行与融合 | [`task_decomposer.py`](multi_fake_news_detector/core/task_decomposer.py)、[`verifier.py`](multi_fake_news_detector/core/verifier.py)、[`fusion.py`](multi_fake_news_detector/core/fusion.py) |
| 图文请求与专家复审 | [`mmr_detect.py`](routes/mmr_detect.py)、[`reexamine.py`](routes/reexamine.py) |

## 本地启动

建议使用 Python 3.11。Windows PowerShell 可执行：

```powershell
git clone https://github.com/hecticlin/mingbian-fact-check.git
cd mingbian-fact-check
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-web.txt
.\.venv\Scripts\python.exe app.py
```

浏览器打开 <http://127.0.0.1:3000/> 可查看首页。未配置数据库时，启动日志会提示数据库预热失败。登录、检测历史和专家复审依赖已初始化的 MySQL 8，完整建表和迁移脚本尚未随本仓库提供。需要持有原项目数据库结构的人员提供空库结构。请避免导入实际用户数据。

配置项参见 [环境变量模板](.env.example)。项目读取进程环境变量。在同一个 PowerShell 窗口中用 `$env:变量名 = '值'` 设置后重启应用。运行纯文本检测需要 DeepSeek 访问凭据，图文检测需要通义 DashScope 访问凭据，博查提供检索上下文。外部检索能力取决于服务和账号，需单独确认返回的证据链接。配置 SMTP 后才可演示邮件找回密码。现有鉴权机制适用于隔离的本地评审环境，部署前需升级旧版密码存储及相关安全配置。

可在仓库根目录执行 `python -m compileall -q app.py routes fake_news_detector multi_fake_news_detector utils` 检查 Python 源码语法。安装依赖后可参考 `python fake_news_detector/main.py --help` 和 `python multi_fake_news_detector/run.py --help` 检查两个模块的命令行入口。

## 版本来源和开发记录

本仓库首个提交是已有项目的脱敏发布基线，后续提交记录本次真实整理及变更。据开发者说明，早期曾用 AI 编程工具辅助编写和修改代码，相关会话已丢失。现存内置提示词可用于说明网页如何调用模型。早期完整 Git 历史和赛题要求的至少三条完整 AI 开发交互链仍缺失。此前的作品材料列有多位作者，个人参赛时须按实际贡献及授权另行申报。

发布版使用环境变量配置凭据，移除了历史日志、上传文件和演示账号材料，使用统一的插画头像并限制公开注册的角色范围。原工程的纯文本模块注明参考《基于大语言模型隐含语义增强的细粒度虚假新闻检测方法》，图文模块注明参考 MMD-Agent 范式。仓库内的第三方组件保留其源码中的授权标识。
