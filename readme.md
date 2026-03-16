
这是一个基于 Streamlit 的 A 股全市场选股与监控仪表盘，专注于“均线粘合 + 趋势突破”策略。

## 🚀 核心功能
- **全市场扫描**：5秒内完成全 A 股 5000+ 标的动态筛选。
- **爆发力评分**：基于量比与粘合度加权的 Top 30 精选逻辑。
- **PostgreSQL 存储**：自动增量同步历史 K 线，摆脱 API 依赖。
- **历史回测**：支持选择过去任意日期进行策略结果复盘。
- **防封机制**：内置重试逻辑、随机延迟与低并发限流。

## 🛠️ 快速启动

### 1. 安装依赖
```bash
pip install streamlit akshare pandas plotly sqlalchemy psycopg2-binary requests streamlit-autorefresh
```

### 2. 运行应用
```bash
python3 -m streamlit run app.py
```

### 3. 配置数据库
1. 启动后，在侧边栏展开 **PostgreSQL 仓库**。
2. 输入您的数据库连接信息（Host, User, Password, Database）。
3. 点击 **保存并测试连接**。

### 4. 初始化数据 (防封关键)
点击侧边栏的 **📦 历史数据大批量补全**。系统将安全地采集历史行情并存入本地。同步后，未来的扫描将优先使用本地库，无需联网抓取，响应极快且绝不封 IP。

## 📁 项目结构
- `app.py`: 主程序代码。
- `stock_data.db`: (旧版) SQLite 备份。
- `walkthrough.md`: 详细的功能演进与运行演示。

## 数据库信息（ PostgreSQL ）
Host: localhost
Port: 5432
User: liangzou (这是您的 Mac 用户名)
Password: (留空即可)
Database: stock_db

## git testing difference
testing 


# 后端启动
cd /Users/liangzou/Desktop/AI_Tools/python-select-stock/backend
source venv_new/bin/activate
uvicorn api:app --port 8000

# 前端启动
cd /Users/liangzou/Desktop/AI_Tools/python-select-stock/backend
source venv_new/bin/activate
uvicorn api:app --port 8000
