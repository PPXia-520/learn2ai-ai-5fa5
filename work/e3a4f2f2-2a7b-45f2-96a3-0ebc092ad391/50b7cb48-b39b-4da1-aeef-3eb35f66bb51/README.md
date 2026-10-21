# 校园开放日报名系统

本地课程原型：报名页面、FastAPI 报名接口、SQLite 持久化存储和真实 HTTP 验证。数据只使用课程虚构样本，不接入真实学校账户系统，也不采集手机号、身份证号或住址。

## 项目结构

- `app/main.py`：报名页面、报名接口和只读查询接口
- `app/db.py`：SQLite 表结构、约束、事务、查重、名额判断和忙碌重试
- `app/static/`：原生 HTML、CSS 和 JavaScript 页面
- `需求依据表.md`：资料版本、适用性、项目选择和验证对应
- `技术检索笔记.md`：SQLite 官方文档链接、访问日期和文档保证范围
- `系统设计.md`：职责边界、规则责任层、接口契约和失败处理
- `验证报告.md`：实际命令、结果、证据和未覆盖范围
- `学习总结.md`：资料判断、AI 协作、核查方法和后续变更检查
- `测试场景与预期.md`：可复现场景的请求、预期和数据库检查条件
- `tests/verify_step1.py`：数据层与报名接口验证
- `tests/verify_page.py`：页面和静态资源验证
- `tests/verify_browser.py`：Chrome 无头加载与渲染验证
- `tests/verify_required_scenarios.py`：按指定场景使用隔离数据库和真实 HTTP 复现
- `verification/`：实际运行的原始结果和服务器日志
- `data/registrations.db`：默认运行数据库，已在 `.gitignore` 中忽略

## 运行时与依赖

- Python 3.13.2
- FastAPI 0.141.1
- Uvicorn 0.52.1

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

## 启动与停止

在项目成果目录中运行：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000/>。

停止服务：在运行服务的终端按 `Ctrl+C`。

如需使用隔离数据库：

```powershell
$env:REGISTRATION_DB = "C:\path\to\registrations-test.db"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

默认数据库路径为 `data/registrations.db`。接口路径：

- `POST /api/registrations`：报名
- `GET /api/registrations/{student_id}`：只读查询报名状态和服务端剩余名额
- `GET /api/health`：健康检查
- `GET /docs`：FastAPI 接口文档

## 验证

`verify_step1.py` 和 `verify_page.py` 使用固定测试端口和临时数据库；`verify_required_scenarios.py` 会为每个场景选择空闲端口并新建临时数据库；`verify_browser.py` 会临时占用 8000 端口。运行浏览器检查前应确保 8000 端口空闲：

```powershell
python tests/verify_step1.py
python tests/verify_page.py
python tests/verify_browser.py
python tests/verify_required_scenarios.py
```

最近一次实际结果：

- 数据层与接口：43 项通过，0 项失败，原始结果在 `verification/step1-results.json`
- 页面与静态资源：13 项通过，0 项失败，原始结果在 `verification/page-results.json`
- 浏览器加载与渲染：HTTP 200，Chrome 返回 0，截图 428,407 字节；原始结果在 `verification/browser-results.json`，截图在 `verification/page-8000.png`
- 按场景复现：9 项通过，0 项失败（S1-S3、S4 五轮、S5）；原始结果在 `verification/required-scenarios-results.json`

验证覆盖输入边界、数据库 CHECK 兜底、重复报名不覆盖姓名、20 次同身份并发、99+20 抢最后名额、3 次忙碌后返回 503、重启持久化，以及页面只在明确 `registered` 结果时显示成功。另用 Chrome 无头加载首页并保存截图，证明 HTML、CSS 和 JavaScript 能在真实浏览器中渲染；该检查不自动点击表单，因此不证明点击交互。`verify_required_scenarios.py` 还按指定场景重新执行了同名、改名、空白身份、99+20 五轮和服务重启；前置 99 条与并发请求都通过真实 HTTP 写入，最终直接读取 SQLite。

## 范围与已知限制

- 固定活动为 `open-day-ai-2026`，名额上限 100。
- 使用课程虚构 `student_id`，不提供真实身份认证，不能阻止用户伪造多个身份。
- 没有支付、通知、候补、取消报名、公网部署或分布式多机一致性。
- 页面是本地单页原型；正式部署前还需要根据实际身份系统、网络和运维条件重新评估。
