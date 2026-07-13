"""Automatic official-announcement discovery; discovered items remain unverified."""
from datetime import datetime
from typing import Any, Dict

from core.db import save_event_catalyst


EVENT_KEYWORDS = ("业绩预告", "业绩快报", "重大合同", "中标", "回购", "增持", "减持", "解禁", "监管问询", "诉讼")


def discover_official_event_catalysts(engine, trade_date: str | None = None) -> Dict[str, Any]:
    import akshare as ak

    selected = (trade_date or datetime.now().strftime("%Y%m%d")).replace("-", "")
    frame = ak.stock_notice_report(symbol="全部", date=selected)
    if frame is None or frame.empty:
        return {"date": selected, "discovered": 0, "saved": 0, "items": []}
    columns = {str(column): column for column in frame.columns}
    code_col = next((value for key, value in columns.items() if "代码" in key), None)
    title_col = next((value for key, value in columns.items() if "公告标题" in key or key == "标题"), None)
    url_col = next((value for key, value in columns.items() if "网址" in key or "链接" in key), None)
    if code_col is None or title_col is None:
        return {"date": selected, "discovered": 0, "saved": 0, "items": [], "error": "公告字段不兼容"}
    items = []
    saved = 0
    for _, row in frame.iterrows():
        title = str(row.get(title_col) or "")
        if not any(keyword in title for keyword in EVENT_KEYWORDS):
            continue
        code = str(row.get(code_col) or "").split(".")[0].zfill(6)
        source_url = str(row.get(url_col) or "") if url_col is not None else ""
        event = {
            "code": code, "event_type": "ANNOUNCEMENT_DISCOVERY",
            "published_at": datetime.strptime(selected, "%Y%m%d"), "title": title,
            "source_url": source_url, "verified": False,
            "metadata": {"discovery_source": "akshare.stock_notice_report", "requires_verification": True},
        }
        saved += int(save_event_catalyst(event, engine))
        items.append({"code": code, "title": title, "source_url": source_url})
    return {"date": selected, "discovered": len(items), "saved": saved, "items": items[:100]}
