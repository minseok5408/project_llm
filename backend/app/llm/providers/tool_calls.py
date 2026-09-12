"""분할된 도구 호출을 제한된 메모리 안에서 조립한다."""

from backend.app.llm.protocol import ProviderUnavailable, ToolCall


class ToolCallBuffer:
    def __init__(self):
        self.calls: dict[int, dict[str, str]] = {}

    def feed(self, values: object) -> None:
        if not isinstance(values, list) or len(values) > 1:
            raise ProviderUnavailable("한 단계에는 도구 호출 한 개만 허용합니다.")
        for value in values:
            if (
                not isinstance(value, dict)
                or type(value.get("index")) is not int
                or value["index"] != 0
            ):
                raise ProviderUnavailable("도구 호출 순서가 올바르지 않습니다.")
            row = self.calls.setdefault(0, {"id": "", "name": "", "arguments": ""})
            if value.get("type") not in (None, "function"):
                raise ProviderUnavailable("지원하지 않는 도구 호출 형식입니다.")
            function = value.get("function") or {}
            if not isinstance(function, dict):
                raise ProviderUnavailable("도구 함수 형식이 올바르지 않습니다.")
            for key, part, cap in (
                ("id", value.get("id"), 200),
                ("name", function.get("name"), 40),
                ("arguments", function.get("arguments"), 4096),
            ):
                if part is None:
                    continue
                if not isinstance(part, str):
                    raise ProviderUnavailable("도구 호출 조각은 문자열이어야 합니다.")
                row[key] += part
                if len(row[key]) > cap:
                    raise ProviderUnavailable("도구 호출 크기 제한을 초과했습니다.")

    def finish(self) -> tuple[ToolCall, ...]:
        return tuple(ToolCall(**row) for row in self.calls.values())
