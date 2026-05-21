"""
Custom ORM tables for stock picks and market diagnosis, stored alongside vnpy's SQLite database
"""
import json
from datetime import datetime
from peewee import Model, CharField, FloatField, IntegerField, TextField, DateTimeField, AutoField
from vnpy_sqlite.sqlite_database import get_file_path


db = None  # initialized by init_db()


def init_db(db_path=None):
    """初始化 peewee database 连接"""
    from peewee import SqliteDatabase as PeeweeSqliteDb
    global db
    path = db_path or get_file_path("database.db")
    db = PeeweeSqliteDb(path)
    StockPick._meta.database = db
    MarketDiagnosis._meta.database = db
    db.connect(reuse_if_open=True)
    db.create_tables([StockPick, MarketDiagnosis], safe=True)
    return db


class StockPick(Model):
    """每日选股记录"""
    pick_date = CharField(max_length=10, index=True)  # YYYYMMDD
    symbol = CharField(max_length=10)
    name = CharField(max_length=20)
    rank = IntegerField(default=0)
    score = FloatField(default=0)
    sub_scores = TextField(default="{}")  # JSON
    main_flow = FloatField(default=0)
    main_ratio = FloatField(default=0)
    chg = FloatField(default=0)
    price = FloatField(default=0)
    mcap_yi = FloatField(default=0)
    industry = CharField(max_length=50, default="")
    cum_3d = FloatField(default=0)
    cum_5d = FloatField(default=0)
    cum_10d = FloatField(default=0)
    analyst_num = IntegerField(default=0)
    ma_align = FloatField(default=0)
    breakout_20d = IntegerField(default=0)
    regime = CharField(max_length=10, default="")
    signals = TextField(default="[]")   # JSON array
    risks = TextField(default="[]")     # JSON array
    next_ret = FloatField(null=True)
    eval_date = CharField(max_length=10, null=True)
    created_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "stock_picks"
        indexes = ((("pick_date", "symbol"), True),)


class MarketDiagnosis(Model):
    """每日盘面诊断"""
    diag_date = CharField(max_length=10, unique=True, index=True)
    stock_count = IntegerField(default=0)
    regime = CharField(max_length=10)
    confidence = FloatField(default=0)
    regime_label = CharField(max_length=50, default="")
    trend_5d = FloatField(default=0)
    up_ratio = FloatField(default=0)
    median_ret = FloatField(default=0)
    pos_flow_ratio = FloatField(default=0)
    total_main_flow = FloatField(default=0)
    total_margin_net = FloatField(default=0)
    risk_level = CharField(max_length=20, default="low")
    risk_alerts = TextField(default="[]")    # JSON
    position_advice = IntegerField(default=50)
    position_base = IntegerField(default=50)
    top_sectors = TextField(default="[]")    # JSON
    bottom_sectors = TextField(default="[]") # JSON
    scores_detail = TextField(default="{}")  # JSON
    # Sentiment fields
    sentiment_score = FloatField(default=50)
    sentiment_label = CharField(max_length=20, default="")
    sentiment_level = CharField(max_length=20, default="")
    sentiment_components = TextField(default="{}")   # JSON
    sentiment_detail = TextField(default="{}")        # JSON
    indices_data = TextField(default="{}")            # JSON index quotes
    created_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = "market_diagnosis"


def save_picks(date_str, picks_data, regime):
    """保存选股结果到数据库，先删后插"""
    StockPick.delete().where(StockPick.pick_date == date_str).execute()
    for p in picks_data:
        StockPick.create(
            pick_date=date_str,
            symbol=p.get("code", ""),
            name=p.get("name", ""),
            rank=p.get("rank", 0),
            score=p.get("score", 0),
            sub_scores=json.dumps(p.get("sub_scores", {}), ensure_ascii=False),
            main_flow=p.get("main_flow", 0),
            main_ratio=p.get("main_ratio", 0),
            chg=p.get("chg", 0),
            price=p.get("price", 0),
            mcap_yi=p.get("market_cap_yi", 0),
            industry=p.get("industry", ""),
            cum_3d=p.get("cum_3d", 0),
            cum_5d=p.get("cum_5d", 0),
            cum_10d=p.get("cum_10d", 0),
            analyst_num=p.get("analyst_num", 0),
            ma_align=p.get("ma_align", 0),
            breakout_20d=1 if p.get("breakout_20d") else 0,
            regime=regime,
            signals=json.dumps(p.get("signals", []), ensure_ascii=False),
            risks=json.dumps(p.get("risks", []), ensure_ascii=False),
        )


def save_diagnosis(date_str, diag):
    """保存盘面诊断到数据库，先删后插"""
    MarketDiagnosis.delete().where(MarketDiagnosis.diag_date == date_str).execute()
    regime = diag.get("regime", {})
    risks = diag.get("risks", {})
    pos = diag.get("position", {})
    sectors = diag.get("sectors", {})
    sentiment = diag.get("sentiment") or {}
    components = sentiment.get("components", {})
    detail = sentiment.get("detail", {})
    indices = sentiment.get("indices") or {}

    MarketDiagnosis.create(
        diag_date=date_str,
        stock_count=diag.get("stock_count", 0),
        regime=regime.get("regime", "unknown"),
        confidence=regime.get("confidence", 0),
        regime_label=regime.get("label", ""),
        trend_5d=regime.get("trend_5d", 0),
        up_ratio=diag.get("breadth", {}).get("up_ratio", 0),
        median_ret=diag.get("breadth", {}).get("median", 0),
        pos_flow_ratio=diag.get("fund_flow", {}).get("pos_flow_ratio", 0),
        total_main_flow=diag.get("fund_flow", {}).get("total_main_flow", 0),
        total_margin_net=diag.get("fund_flow", {}).get("total_margin_net", 0),
        risk_level=risks.get("level", "low"),
        risk_alerts=json.dumps(risks.get("alerts", []), ensure_ascii=False),
        position_advice=pos.get("adjusted", 50),
        position_base=pos.get("base", 50),
        top_sectors=json.dumps(
            [s["name"] for s in sectors.get("top_industries", [])[:5]], ensure_ascii=False
        ),
        bottom_sectors=json.dumps(
            [s["name"] for s in sectors.get("bottom_industries", [])[:5]], ensure_ascii=False
        ),
        scores_detail=json.dumps(regime.get("scores", {}), ensure_ascii=False),
        sentiment_score=sentiment.get("score", 50),
        sentiment_label=sentiment.get("label", ""),
        sentiment_level=sentiment.get("level", "neutral"),
        sentiment_components=json.dumps(components, ensure_ascii=False),
        sentiment_detail=json.dumps(detail, ensure_ascii=False),
        indices_data=json.dumps(indices, ensure_ascii=False),
    )


def get_latest_picks(limit=5):
    """获取最近选股"""
    return list(
        StockPick.select()
        .order_by(StockPick.pick_date.desc(), StockPick.rank.asc())
        .limit(limit)
    )


def get_picks_by_date(date_str):
    """获取指定日期的选股"""
    return list(
        StockPick.select()
        .where(StockPick.pick_date == date_str)
        .order_by(StockPick.rank.asc())
    )


def get_latest_diagnosis():
    """获取最近一次盘面诊断"""
    return MarketDiagnosis.select().order_by(MarketDiagnosis.diag_date.desc()).first()
