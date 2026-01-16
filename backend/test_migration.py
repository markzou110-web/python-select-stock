#!/usr/bin/env python3
"""测试数据库迁移"""
import sys
sys.path.insert(0, '/Users/liangzou/Desktop/AI_Tools/python-select-stock/backend')

from core.db import get_db_engine, init_db
from sqlalchemy import text

# 初始化数据库（会执行 ALTER TABLE）
engine = get_db_engine()
print('数据库引擎:', engine)
init_db(engine)

# 验证新字段已添加
with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'daily_k'
        AND column_name IN ('main_net_inflow', 'super_large_net', 'large_net', 'medium_net', 'small_net')
        ORDER BY column_name
    """))
    print('\n新增的资金流字段:')
    rows = list(result)
    if rows:
        for row in rows:
            print(f'  - {row[0]}: {row[1]}')
        print(f'\n✅ 成功添加 {len(rows)} 个资金流字段')
    else:
        print('  ⚠️ 未找到新字段')
