"""제한 계산기의 산술·Python 의미·외부 접근 차단·자원 상한을 검증한다."""

import pytest

from backend.app.tools.calculation import CalculationError, evaluate_expression, trace_python


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("18_000 * 3 * 0.85 + 2500", "48400"),
        ("0.1 + 0.2", "0.3"),
        ("50000 * 0.8 * 0.9", "36000"),
        ("1.75 * 1000 - 350 - 0.60 * 1000", "800"),
        ("50000 - 18750 + 6250 - 9000", "28500"),
        ("(4 * 12 + 6 * 17) / 10", "15"),
        ("-8 // 3", "-3"),
        ("8 // -3", "-3"),
        ("-8 // -3", "2"),
        ("-8 % 3", "1"),
        ("8 % -3", "-1"),
        ("1.5 // 0.4", "3"),
        ("-1.5 % 0.4", "0.1"),
        ("2 ** -3", "0.125"),
        ("0 ** 0", "1"),
        ("-(2 + 3) * +4", "-20"),
        ("1.2e2 + 5e-2", "120.05"),
        ("-0.0", "0"),
    ],
)
def test_decimal_arithmetic_is_exact(source, expected):
    assert evaluate_expression(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "values = [2, 4, 6]\nprint(sum(value // 2 for value in values if value > 2))",
            "5\n",
        ),
        ("print([v for v in [1, 4, 7, 10, 13] if v > 5 and v % 2 == 1])", "[7, 13]\n"),
        ("print(list(map(lambda v: v * 3 - 1, [2, 5, 8])))", "[5, 14, 23]\n"),
        ("print([v * 2 for v in sorted([3, 1, 4, 2], reverse=True)[1:]])", "[6, 4, 2]\n"),
        ("print([v // 3 for v in [-8, -1, 1, 8]])", "[-3, -1, 0, 2]\n"),
        ("print([v % 3 for v in [-8, -1, 1, 8]])", "[1, 2, 1, 2]\n"),
        ("print(False and 1 / 0, True or 1 / 0)", "False True\n"),
        ("print(0 and missing, 7 or missing)", "0 7\n"),
        ("print(3 < 2 < (1 / 0))", "False\n"),
        ("print([v for v in range(-3, 4) if v != 0 if 6 // v > 0])", "[1, 2, 3]\n"),
        ("print(list(range(8, 0, -2)), len('가나다'))", "[8, 6, 4, 2] 3\n"),
        ("print([1, 2, 3, 4][::-1], 'abcdef'[1:5:2])", "[4, 3, 2, 1] bd\n"),
        ("print(4 in [2, 4], 5 not in [2, 4], not [])", "True True True\n"),
        ("print('안녕', '세계', sep=' / ', end='!')\nprint()", "안녕 / 세계!\n"),
        ("print(['가', '나'], (1,), ())", "['가', '나'] (1,) ()\n"),
        ("print([1] * 3 + [2], '가' * 2)", "[1, 1, 1, 2] 가가\n"),
        ("x = 10\nprint([x for x in [1, 2]])\nprint(x)", "[1, 2]\n10\n"),
        ("g = (x * 2 for x in [1, 2])\nprint(list(g))\nprint(list(g))", "[2, 4]\n[]\n"),
        ("offset = 1\ng = (x + offset for x in [1, 2])\noffset = 5\nprint(list(g))", "[6, 7]\n"),
        ("a = [1, 2]\ng = (x for x in a)\na = [9]\nprint(list(g))", "[1, 2]\n"),
        (
            "offset = 1\nm = map(lambda x: x + offset, [1, 2])\noffset = 4\nprint(list(m))",
            "[5, 6]\n",
        ),
        ("g = (x for x in [2, 1])\nprint(sorted(list(g), reverse=sum(g)))", "[1, 2]\n"),
        ("print(sum([1e16, 1.0, -1e16]))", "1.0\n"),
        ("print(sum([], 3), list(), sum(range(4), 10))", "3 [] 16\n"),
        ("print(0.1 + 0.2)", "0.30000000000000004\n"),
        ("print(2 ** 2.0, 2.0 ** 0, 2 ** 0.0)", "4.0 1.0 1.0\n"),
        (
            "mapped = map(lambda x: x + 1, [1, 2])\nprint(list(mapped))\nprint(list(mapped))",
            "[2, 3]\n[]\n",
        ),
        (
            "m = [map(lambda x: x + i, [0]) for i in [1, 2]]\nprint(list(m[0]), list(m[1]))",
            "[2] [2]\n",
        ),
        (
            "g = ((i for j in [0]) for i in [1, 2])\na = list(g)\nprint(list(a[0]), list(a[1]))",
            "[2] [2]\n",
        ),
        (
            "offset = 1\nm = map(lambda x: (offset for j in [0]), [0])\n"
            "a = list(m)\noffset = 5\nprint(list(a[0]))",
            "[5]\n",
        ),
        ("value = 1", ""),
    ],
)
def test_python_trace_preserves_supported_semantics(source, expected):
    assert trace_python(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "",
        " ",
        "x + 1",
        "'2' + '3'",
        "True + 1",
        "[1, 2]",
        "sum([1, 2])",
        "0x10 + 1",
        "1 / 0",
        "1 / 3",
        "1e-81",
        "1e80",
        "0e-1000",
        "0e1000",
        "0e-1000000000",
        "0e1000000000",
        "2 ** 17",
        "2 ** -17",
        "4 ** 0.5",
        "1e79 * 100",
        "1e-79 / 100",
        "9" * 81,
        "1 + " * 100 + "1",
        "(" * 40 + "1" + ")" * 40 + "+" * 33 + "1",
        "1" * 4097,
    ],
)
def test_decimal_rejects_unsupported_or_inexact_inputs(source):
    with pytest.raises(CalculationError):
        evaluate_expression(source)


@pytest.mark.parametrize(
    "source",
    [
        "import os\nprint(1)",
        "from pathlib import Path\nprint(1)",
        "print(open('/tmp/example').read())",
        "print(__import__('os'))",
        "print((1).__class__)",
        "print(getattr(1, '__class__'))",
        "print(eval('1+1'))",
        "print(exec('a=1'))",
        "print(compile('1', '<input>', 'eval'))",
        "print(globals())",
        "print((lambda x: x)(1))",
        "def f():\n    return 1\nprint(f())",
        "for x in [1]:\n    print(x)",
        "while True:\n    print(1)",
        "a = [1]\na.append(2)",
        "a = [1]\na[0] = 2\nprint(a)",
        "a, b = [1, 2]\nprint(a)",
        "a = b = 1\nprint(a)",
        "a: int = 1\nprint(a)",
        "a = 1\na += 1\nprint(a)",
        "print([x*y for x in [1] for y in [2]])",
        "print([x for x, y in [(1, 2)]])",
        "print(list(map(lambda x, y: x + y, [1], [2])))",
        "print(list(map(lambda x=1: x, [2])))",
        "print(list(map(str, [1])))",
        "print(sorted([1, 2], key=lambda x: -x))",
        "print('x', file=1)",
        "print(*[1, 2])",
        "print(f'{1}')",
        "print({'x': 1})",
        "print({1, 2})",
        "print(b'bytes')",
        "print(1j)",
        "print(1 << 2)",
        "print(1 if True else 2)",
        "sum = 1\nprint(sum([1, 2]))",
        "print = 1\nprint(2)",
        "print(unknown)",
        "print(1 / 0)",
        "print([1][2])",
        "print([1][::0])",
        "print(sorted([1, 'a']))",
        "print(sum(['x']))",
        "print(list(1))",
        "print(range(0, 1, 0))",
        "print((x for x in [1]))",
        "print(len(x for x in [1]))",
        "g = (x for x in 1)\nprint(9)",
        "m = map(lambda x: x, 1)\nprint(9)",
    ],
)
def test_python_trace_rejects_external_access_and_unsupported_semantics(source):
    with pytest.raises(CalculationError):
        trace_python(source)


@pytest.mark.parametrize(
    "source",
    [
        "print(list(range(129)))",
        "print(list(range(10 ** 16)))",
        "print([0] * 129)",
        "print([0] * (10 ** 16))",
        "print('x' * 1025)",
        "print('x' * (10 ** 16))",
        "print(10 ** 16 ** 16)",
        "print(1e100)",
        "print(1e79 * 100)",
        "print(2 ** 17)",
        "print('x' * 1024)\n" * 5,
        "print([('x' * 100) for x in range(100)])",
        "a = [1]\n" + "a = [a, a]\n" * 30 + "print(a)",
        "print([" + ",".join("1" for _ in range(129)) + "])",
        "print(" + "+" * 33 + "1)",
        "print(1)\n" * 130,
        "print(1)" + " " * 4097,
    ],
)
def test_python_resource_limits_fail_closed(source):
    with pytest.raises(CalculationError):
        trace_python(source)


def test_failed_trace_does_not_return_partial_stdout():
    with pytest.raises(CalculationError):
        trace_python("print('앞부분')\nprint(1 / 0)")


def test_generator_uses_shared_operation_budget_during_consumption():
    with pytest.raises(CalculationError, match="연산"):
        trace_python("print(sum(sum(range(128)) for x in range(128)))")


def test_decimal_floor_and_remainder_match_python_for_both_divisor_signs():
    for numerator in range(-8, 9):
        for denominator in (-5, -3, -1, 1, 3, 5):
            assert evaluate_expression(f"{numerator} // {denominator}") == str(
                numerator // denominator
            )
            assert evaluate_expression(f"{numerator} % {denominator}") == str(
                numerator % denominator
            )
