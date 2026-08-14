from sqlalchemy import create_engine, text

from core.models import Base
from routers.sync import _load_local_sync_codes


def test_local_stock_list_is_available_without_network():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO stock_basic (code, name, industry)
            VALUES ('603259', '药明康德', '医疗服务'),
                   ('000001', '平安银行', '银行'),
                   ('920001', '北交测试', '测试')
        """))

    assert _load_local_sync_codes(engine) == ['000001', '603259']
