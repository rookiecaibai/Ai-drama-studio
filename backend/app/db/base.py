"""ORM 基类。

所有模型都继承 Base，SQLAlchemy 靠 Base.metadata 收集"有哪些表"，
所以 create_all / Alembic 迁移才能知道要建什么。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
