"""厂商私有参数解析的测试。

配置项写错（不是合法 JSON、写成了数组……）不该把服务搞崩 ——
这是"用户会填错"的典型场景，容错必须在测试里锁住。
"""

from app.services.llm.params import parse_extra_params


def test_valid_object_is_parsed():
    assert parse_extra_params('{"watermark_enabled": false}', name="X") == {"watermark_enabled": False}
    assert parse_extra_params('{"a": 1, "b": "c"}', name="X") == {"a": 1, "b": "c"}


def test_empty_returns_empty_dict():
    assert parse_extra_params("", name="X") == {}
    assert parse_extra_params("   ", name="X") == {}
    assert parse_extra_params(None, name="X") == {}  # type: ignore[arg-type]


def test_invalid_json_is_ignored_not_crashing():
    """写错了就当没配 —— 记一条警告即可，不能让整个接口 500。"""
    assert parse_extra_params("{不是json}", name="X") == {}
    assert parse_extra_params('{"unclosed": ', name="X") == {}


def test_non_object_json_is_ignored():
    assert parse_extra_params("[1, 2, 3]", name="X") == {}
    assert parse_extra_params('"just a string"', name="X") == {}
    assert parse_extra_params("123", name="X") == {}
