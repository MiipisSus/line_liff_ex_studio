import pytest

from booking.services import merge_intervals, subtract_intervals


@pytest.mark.parametrize(
    "given, expected",
    [
        ([], []),
        ([(1, 3)], [(1, 3)]),
        ([(5, 7), (1, 3)], [(1, 3), (5, 7)]),
        ([(1, 3), (2, 5)], [(1, 5)]),
        ([(1, 3), (3, 5)], [(1, 5)]),  # 相接也合併
        ([(1, 10), (2, 3)], [(1, 10)]),  # 包含
    ],
)
def test_merge_intervals(given, expected):
    assert merge_intervals(given) == expected


@pytest.mark.parametrize(
    "base, removals, expected",
    [
        ([(1, 10)], [], [(1, 10)]),
        ([(1, 10)], [(3, 5)], [(1, 3), (5, 10)]),  # 中間挖洞
        ([(1, 10)], [(0, 3)], [(3, 10)]),  # 切掉開頭
        ([(1, 10)], [(8, 12)], [(1, 8)]),  # 切掉結尾
        ([(1, 10)], [(0, 12)], []),  # 全部扣除
        ([(1, 10)], [(10, 12)], [(1, 10)]),  # 只相接，不影響
        ([(1, 5), (6, 10)], [(4, 7)], [(1, 4), (7, 10)]),  # 跨兩段
        ([(1, 10)], [(2, 3), (5, 6)], [(1, 2), (3, 5), (6, 10)]),
        ([(1, 10)], [(5, 6), (2, 3)], [(1, 2), (3, 5), (6, 10)]),  # removals 未排序
        ([(1, 5), (3, 8)], [(4, 6)], [(1, 4), (6, 8)]),  # base 有重疊
    ],
)
def test_subtract_intervals(base, removals, expected):
    assert subtract_intervals(base, removals) == expected
