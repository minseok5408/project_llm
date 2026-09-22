"""허용한 AST만 직접 해석하고 계산·반복·출력 자원을 제한한다."""

import ast
import math
from collections import ChainMap
from collections.abc import Iterator
from decimal import Decimal, DecimalException, Inexact, localcontext
from typing import Any

MAX_SOURCE = 4096
MAX_NODES = 512
MAX_DEPTH = 32
MAX_OPERATIONS = 4096
MAX_ITEMS = 128
MAX_STRING = 1024
MAX_OUTPUT = 4096
MAX_DIGITS = 80
MAX_EXPONENT = 16
MAX_NUMBER = 10**MAX_DIGITS


class CalculationError(ValueError):
    """미지원 문법·잘못된 연산·자원 제한으로 계산을 확정할 수 없다."""


class _IteratorValue:
    """생성기와 map의 지연 평가 및 한 번만 소비되는 동작을 보존한다."""

    def __init__(self, values: Iterator[Any]) -> None:
        self.values = values


_ARITHMETIC_NODES = {
    ast.Expression,
    ast.Constant,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.UAdd,
    ast.USub,
}
_PYTHON_NODES = _ARITHMETIC_NODES | {
    ast.Module,
    ast.Assign,
    ast.Expr,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.List,
    ast.Tuple,
    ast.Call,
    ast.keyword,
    ast.Subscript,
    ast.Slice,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.Not,
    ast.ListComp,
    ast.GeneratorExp,
    ast.comprehension,
    ast.Lambda,
    ast.arguments,
    ast.arg,
}
_CALLS = {"print", "sum", "list", "range", "len", "sorted", "map"}


def _parse(source: str, *, python: bool) -> ast.AST:
    if not isinstance(source, str) or not source.strip() or len(source) > MAX_SOURCE:
        raise CalculationError("입력은 비어 있지 않은 4,096자 이하 문자열이어야 합니다.")
    try:
        tree = ast.parse(source, mode="exec" if python else "eval")
    except (SyntaxError, ValueError, RecursionError) as error:
        raise CalculationError("해석할 수 없는 문법입니다.") from error
    stack = [(tree, 1)]
    count = 0
    allowed = _PYTHON_NODES if python else _ARITHMETIC_NODES
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise CalculationError("AST 크기 또는 중첩 깊이 한도를 초과했습니다.")
        if type(node) not in allowed:
            raise CalculationError("지원하지 않는 문법이 포함되어 있습니다.")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in _CALLS
        ):
            raise CalculationError("허용되지 않은 함수 호출입니다.")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise CalculationError("특수 이름에는 접근할 수 없습니다.")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return tree


class _Evaluator:
    def __init__(self, source: str, *, decimal: bool = False) -> None:
        self.source = source
        self.decimal = decimal
        self.operations = 0
        self.output: list[str] = []
        self.output_size = 0

    def tick(self, amount: int = 1) -> None:
        self.operations += amount
        if self.operations > MAX_OPERATIONS:
            raise CalculationError("연산 횟수 한도를 초과했습니다.")

    def checked(self, value: Any, depth: int = 0) -> Any:
        self.tick()
        if depth > MAX_DEPTH:
            raise CalculationError("값의 중첩 깊이 한도를 초과했습니다.")
        if type(value) is bool:
            return value
        if type(value) is int:
            if abs(value) >= MAX_NUMBER:
                raise CalculationError("정수 크기 한도를 초과했습니다.")
        elif type(value) is float:
            if not math.isfinite(value) or abs(value) >= MAX_NUMBER:
                raise CalculationError("유한한 범위의 수만 지원합니다.")
        elif isinstance(value, Decimal):
            if (
                not value.is_finite()
                or len(value.as_tuple().digits) > MAX_DIGITS
                or abs(value.as_tuple().exponent) > MAX_DIGITS
                or (value and not -MAX_DIGITS <= value.adjusted() < MAX_DIGITS)
            ):
                raise CalculationError("십진수 크기 또는 정밀도 한도를 초과했습니다.")
        elif type(value) is str:
            if len(value) > MAX_STRING:
                raise CalculationError("문자열 크기 한도를 초과했습니다.")
        elif type(value) in (list, tuple, range):
            if len(value) > MAX_ITEMS:
                raise CalculationError("컬렉션은 128개 원소까지만 지원합니다.")
            if not isinstance(value, range):
                for item in value:
                    self.checked(item, depth + 1)
        elif not isinstance(value, _IteratorValue):
            raise CalculationError("지원하지 않는 값의 형식입니다.")
        return value

    def constant(self, node: ast.Constant) -> Any:
        value = node.value
        if self.decimal:
            if type(value) not in (int, float):
                raise CalculationError("계산식에는 숫자만 사용할 수 있습니다.")
            literal = ast.get_source_segment(self.source, node) or ""
            if len(literal) > MAX_DIGITS + 16:
                raise CalculationError("숫자 리터럴이 너무 깁니다.")
            value = Decimal(literal.replace("_", ""))
        elif type(value) not in (int, float, str, bool):
            raise CalculationError("지원하지 않는 상수입니다.")
        return self.checked(value)

    def binary(self, operation: ast.operator, left: Any, right: Any) -> Any:
        self.tick()
        if (
            isinstance(operation, ast.Add)
            and type(left) is type(right)
            and type(left) in (list, tuple, str)
        ):
            maximum = MAX_STRING if isinstance(left, str) else MAX_ITEMS
            if len(left) + len(right) > maximum:
                raise CalculationError("결합 결과의 크기 한도를 초과했습니다.")
            return self.checked(left + right)
        if isinstance(operation, ast.Mult):
            sequence, count = (left, right) if type(right) in (int, bool) else (right, left)
            if type(sequence) in (list, tuple, str) and type(count) in (int, bool):
                maximum = MAX_STRING if isinstance(sequence, str) else MAX_ITEMS
                if len(sequence) * max(0, count) > maximum:
                    raise CalculationError("반복 결과의 크기 한도를 초과했습니다.")
                return self.checked(sequence * max(0, count))
        number_types = (Decimal,) if self.decimal else (int, float, bool)
        if not isinstance(left, number_types) or not isinstance(right, number_types):
            raise CalculationError("이 연산은 숫자에만 적용할 수 있습니다.")
        if isinstance(operation, ast.Add):
            value = left + right
        elif isinstance(operation, ast.Sub):
            value = left - right
        elif isinstance(operation, ast.Mult):
            value = left * right
        elif isinstance(operation, ast.Div):
            value = left / right
        elif isinstance(operation, (ast.FloorDiv, ast.Mod)) and self.decimal:
            quotient = left // right
            if left % right and (left < 0) != (right < 0):
                quotient -= 1
            value = quotient if isinstance(operation, ast.FloorDiv) else left - quotient * right
        elif isinstance(operation, ast.FloorDiv):
            value = left // right
        elif isinstance(operation, ast.Mod):
            value = left % right
        elif isinstance(operation, ast.Pow):
            if right != int(right) or abs(right) > MAX_EXPONENT:
                raise CalculationError("거듭제곱 지수는 -16부터 16까지의 정수만 지원합니다.")
            if self.decimal:
                value = Decimal(1) if right == 0 else left ** int(right)
            else:
                value = left**right
        else:
            raise CalculationError("지원하지 않는 산술 연산입니다.")
        return self.checked(value)

    def values(self, value: Any) -> Iterator[Any]:
        if isinstance(value, _IteratorValue):
            iterator = value.values
        elif type(value) in (list, tuple, range, str):
            iterator = iter(value)
        else:
            raise CalculationError("반복할 수 없는 값입니다.")

        def produce() -> Iterator[Any]:
            for index, item in enumerate(iterator):
                self.tick()
                if index >= MAX_ITEMS:
                    raise CalculationError("반복 원소 한도를 초과했습니다.")
                yield self.checked(item)

        return produce()

    def comprehension(self, node: ast.ListComp | ast.GeneratorExp, env: dict) -> Any:
        if len(node.generators) != 1:
            raise CalculationError("컴프리헨션은 단일 for만 지원합니다.")
        clause = node.generators[0]
        if not isinstance(clause.target, ast.Name) or clause.is_async:
            raise CalculationError("컴프리헨션 대상은 단일 이름이어야 합니다.")
        iterable = self.values(self.expression(clause.iter, env))
        # 반복 변수는 같은 지역 범위를 공유하고 외부 이름은 소비 시점에 조회한다.
        local = ChainMap({}, env)

        def produce() -> Iterator[Any]:
            for item in iterable:
                local[clause.target.id] = item
                if all(self.expression(condition, local) for condition in clause.ifs):
                    yield self.expression(node.elt, local)

        result = _IteratorValue(produce())
        return self.checked(list(self.values(result))) if isinstance(node, ast.ListComp) else result

    def call(self, node: ast.Call, env: dict) -> Any:
        name = node.func.id
        if name in env or name == "print":
            raise CalculationError("이 위치에서는 함수를 호출할 수 없습니다.")
        keyword_names = set()
        for keyword in node.keywords:
            if name != "sorted" or keyword.arg != "reverse" or keyword.arg in keyword_names:
                raise CalculationError("지원하지 않는 함수 인자입니다.")
            keyword_names.add(keyword.arg)
        if name == "map":
            if len(node.args) != 2 or not isinstance(node.args[0], ast.Lambda):
                raise CalculationError("map은 단일 인자 lambda와 반복 값만 지원합니다.")
            function = node.args[0]
            arguments = function.args
            if (
                len(arguments.args) != 1
                or arguments.posonlyargs
                or arguments.kwonlyargs
                or arguments.vararg
                or arguments.kwarg
                or arguments.defaults
            ):
                raise CalculationError("lambda는 기본값 없는 단일 인자만 지원합니다.")
            iterable = self.values(self.expression(node.args[1], env))

            def produce() -> Iterator[Any]:
                for item in iterable:
                    yield self.expression(
                        function.body, ChainMap({arguments.args[0].arg: item}, env)
                    )

            return _IteratorValue(produce())
        args = [self.expression(argument, env) for argument in node.args]
        keywords = {keyword.arg: self.expression(keyword.value, env) for keyword in node.keywords}
        if (
            name == "range"
            and 1 <= len(args) <= 3
            and all(type(value) in (int, bool) for value in args)
        ):
            return self.checked(range(*args))
        if name == "list" and len(args) <= 1:
            return self.checked(list(self.values(args[0])) if args else [])
        if name == "len" and len(args) == 1 and type(args[0]) in (list, tuple, range, str):
            return len(args[0])
        if name == "sum" and 1 <= len(args) <= 2:
            start = args[1] if len(args) == 2 else 0
            if type(start) not in (int, float, bool):
                raise CalculationError("sum은 숫자만 합산할 수 있습니다.")
            numbers = []
            for value in self.values(args[0]):
                if type(value) not in (int, float, bool):
                    raise CalculationError("sum은 숫자만 합산할 수 있습니다.")
                numbers.append(value)
            self.tick(len(numbers))
            # Python 3.12의 부동소수 합산 보정까지 동일하게 적용한다.
            return self.checked(sum(numbers, start))
        if name == "sorted" and len(args) == 1:
            reverse = keywords.get("reverse", False)
            if type(reverse) not in (int, bool):
                raise CalculationError("reverse는 논리값 또는 정수여야 합니다.")
            items = list(self.values(args[0]))
            self.tick(len(items) * max(1, len(items).bit_length()))
            return self.checked(sorted(items, reverse=bool(reverse)))
        raise CalculationError("지원하지 않는 함수 인자입니다.")

    def compare(self, operation: ast.cmpop, left: Any, right: Any) -> bool:
        self.tick()
        if isinstance(left, _IteratorValue) or isinstance(right, _IteratorValue):
            raise CalculationError("반복자 비교는 지원하지 않습니다.")
        if isinstance(operation, ast.Eq):
            return left == right
        if isinstance(operation, ast.NotEq):
            return left != right
        if isinstance(operation, ast.Lt):
            return left < right
        if isinstance(operation, ast.LtE):
            return left <= right
        if isinstance(operation, ast.Gt):
            return left > right
        if isinstance(operation, ast.GtE):
            return left >= right
        if isinstance(operation, (ast.In, ast.NotIn)) and type(right) in (list, tuple, str, range):
            return left in right if isinstance(operation, ast.In) else left not in right
        raise CalculationError("지원하지 않는 비교입니다.")

    def expression(self, node: ast.AST, env: dict) -> Any:
        self.tick()
        if isinstance(node, ast.Constant):
            return self.constant(node)
        if isinstance(node, ast.Name):
            if node.id not in env:
                raise CalculationError("정의되지 않은 이름입니다.")
            return env[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            if len(node.elts) > MAX_ITEMS:
                raise CalculationError("컬렉션 원소 한도를 초과했습니다.")
            values = [self.expression(item, env) for item in node.elts]
            return self.checked(tuple(values) if isinstance(node, ast.Tuple) else values)
        if isinstance(node, ast.BinOp):
            return self.binary(
                node.op, self.expression(node.left, env), self.expression(node.right, env)
            )
        if isinstance(node, ast.UnaryOp):
            value = self.expression(node.operand, env)
            if isinstance(node.op, ast.Not):
                return not value
            if not isinstance(value, (int, float, Decimal)):
                raise CalculationError("부호 연산에는 숫자가 필요합니다.")
            return self.checked(-value if isinstance(node.op, ast.USub) else +value)
        if isinstance(node, ast.BoolOp):
            for item in node.values:
                value = self.expression(item, env)
                if (isinstance(node.op, ast.And) and not value) or (
                    isinstance(node.op, ast.Or) and value
                ):
                    return value
            return value
        if isinstance(node, ast.Compare):
            left = self.expression(node.left, env)
            for operation, comparator in zip(node.ops, node.comparators, strict=True):
                right = self.expression(comparator, env)
                if not self.compare(operation, left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            return self.call(node, env)
        if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
            return self.comprehension(node, env)
        if isinstance(node, ast.Subscript):
            value = self.expression(node.value, env)
            if type(value) not in (list, tuple, str, range):
                raise CalculationError("지원하지 않는 인덱스 대상입니다.")
            if isinstance(node.slice, ast.Slice):
                parts = [
                    self.expression(part, env) if part is not None else None
                    for part in (node.slice.lower, node.slice.upper, node.slice.step)
                ]
                if any(part is not None and type(part) not in (int, bool) for part in parts):
                    raise CalculationError("슬라이스에는 정수가 필요합니다.")
                index = slice(*parts)
            else:
                index = self.expression(node.slice, env)
                if type(index) not in (int, bool):
                    raise CalculationError("인덱스에는 정수가 필요합니다.")
            return self.checked(value[index])
        raise CalculationError("지원하지 않는 표현식입니다.")

    def format_value(self, value: Any, *, nested: bool = False, depth: int = 0) -> str:
        self.tick()
        if depth > MAX_DEPTH or isinstance(value, _IteratorValue):
            raise CalculationError("해당 값의 표준 출력은 지원하지 않습니다.")
        if type(value) in (list, tuple):
            parts = []
            total = 2
            for item in value:
                text = self.format_value(item, nested=True, depth=depth + 1)
                total += len(text) + 2
                if total > MAX_OUTPUT:
                    raise CalculationError("출력 크기 한도를 초과했습니다.")
                parts.append(text)
            body = ", ".join(parts)
            if isinstance(value, tuple):
                return "(" + body + ("," if len(value) == 1 else "") + ")"
            return "[" + body + "]"
        return repr(value) if nested else str(value)

    def print_call(self, node: ast.Call, env: dict) -> None:
        if "print" in env:
            raise CalculationError("덮어쓴 print는 호출할 수 없습니다.")
        values = [self.expression(argument, env) for argument in node.args]
        options = {"sep": " ", "end": "\n"}
        seen = set()
        for keyword in node.keywords:
            if keyword.arg not in options or keyword.arg in seen:
                raise CalculationError("print는 sep와 end만 지원합니다.")
            option = self.expression(keyword.value, env)
            if type(option) is not str:
                raise CalculationError("print 구분자는 문자열이어야 합니다.")
            options[keyword.arg] = option
            seen.add(keyword.arg)
        text = options["sep"].join(self.format_value(value) for value in values) + options["end"]
        self.output_size += len(text)
        if self.output_size > MAX_OUTPUT:
            raise CalculationError("출력은 4,096자까지만 지원합니다.")
        self.output.append(text)


def evaluate_expression(source: str) -> str:
    """정확한 십진 산술 결과를 반환하고 순환 소수·범위 초과는 거부한다."""
    try:
        tree = _parse(source, python=False)
        with localcontext() as context:
            context.prec = MAX_DIGITS
            context.Emax = MAX_DIGITS
            context.Emin = -MAX_DIGITS
            context.traps[Inexact] = True
            result = _Evaluator(source, decimal=True).expression(tree.body, {})
            if not result:
                return "0"
            text = format(result, "f")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return text
    except CalculationError:
        raise
    except (ArithmeticError, DecimalException, TypeError, ValueError, RecursionError) as error:
        raise CalculationError("정확한 십진수로 계산할 수 없는 식입니다.") from error


def trace_python(source: str) -> str:
    """제한된 Python 문법을 직접 해석해 print의 표준 출력만 반환한다."""
    try:
        tree = _parse(source, python=True)
        evaluator = _Evaluator(source)
        env = {}
        for statement in tree.body:
            evaluator.tick()
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                env[statement.targets[0].id] = evaluator.expression(statement.value, env)
            elif (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Name)
                and statement.value.func.id == "print"
            ):
                evaluator.print_call(statement.value, env)
            else:
                raise CalculationError("단일 이름 대입과 print 문장만 지원합니다.")
        return "".join(evaluator.output)
    except CalculationError:
        raise
    except (ArithmeticError, TypeError, ValueError, IndexError, KeyError, RecursionError) as error:
        raise CalculationError("지원 범위에서 Python 출력을 확정할 수 없습니다.") from error
