from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from loro.agraph.reference_validator import TOKEN_RE, AgxError


class ExpressionError(ValueError):
    """Raised when AGX cannot be parsed or evaluated with strict typing."""


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str


class Evaluator:
    def __init__(self, text: str, scope: Mapping[str, Any]) -> None:
        self.tokens = self._tokenize(text)
        self.pos = 0
        self.scope = scope

    @staticmethod
    def _tokenize(text: str) -> list[_Token]:
        result: list[_Token] = []
        index = 0
        while index < len(text):
            if text[index].isspace():
                index += 1
                continue
            match = TOKEN_RE.match(text, index)
            if not match:
                raise ExpressionError(f"unexpected character {text[index]!r} at offset {index}")
            index = match.end()
            for kind in ("number", "string", "op", "name"):
                value = match.group(kind)
                if value is not None:
                    result.append(_Token(kind, value))
                    break
        return result

    def parse(self) -> Any:
        value = self._or()
        if self._peek() is not None:
            raise ExpressionError(f"trailing input at {self._peek().value!r}")
        return value

    def _or(self) -> Any:
        value = self._and()
        while self._match("op", "||") or self._match("name", "or"):
            right = self._and()
            value = self._boolean(value, "or") or self._boolean(right, "or")
        return value

    def _and(self) -> Any:
        value = self._in()
        while self._match("op", "&&") or self._match("name", "and"):
            right = self._in()
            value = self._boolean(value, "and") and self._boolean(right, "and")
        return value

    def _in(self) -> Any:
        value = self._equality()
        while self._match("name", "in"):
            container = self._equality()
            if not isinstance(container, (str, list, dict)):
                raise ExpressionError("right operand of 'in' must be string, array, or object")
            value = value in container
        return value

    def _equality(self) -> Any:
        value = self._comparison()
        while (token := self._peek()) and token.value in {"==", "!="}:
            self.pos += 1
            right = self._comparison()
            self._same_type(value, right, token.value)
            value = value == right if token.value == "==" else value != right
        return value

    def _comparison(self) -> Any:
        value = self._additive()
        while (token := self._peek()) and token.value in {"<", "<=", ">", ">="}:
            self.pos += 1
            right = self._additive()
            self._same_type(value, right, token.value)
            if not isinstance(value, (int, float, str)) or isinstance(value, bool):
                raise ExpressionError(f"{token.value} requires numbers or strings")
            if token.value == "<":
                value = value < right
            elif token.value == "<=":
                value = value <= right
            elif token.value == ">":
                value = value > right
            else:
                value = value >= right
        return value

    def _additive(self) -> Any:
        value = self._multiplicative()
        while (token := self._peek()) and token.value in {"+", "-"}:
            self.pos += 1
            right = self._multiplicative()
            self._same_type(value, right, token.value)
            if token.value == "+" and isinstance(value, (int, float, str)):
                value = value + right
            elif token.value == "-" and self._number(value) and self._number(right):
                value = value - right
            else:
                raise ExpressionError(f"invalid operands for {token.value}")
        return value

    def _multiplicative(self) -> Any:
        value = self._unary()
        while (token := self._peek()) and token.value in {"*", "/", "%"}:
            self.pos += 1
            right = self._unary()
            if not self._number(value) or not self._number(right):
                raise ExpressionError(f"{token.value} requires numeric operands")
            if token.value == "*":
                value *= right
            elif token.value == "/":
                value /= right
            else:
                value %= right
        return value

    def _unary(self) -> Any:
        if self._match("op", "!") or self._match("name", "not"):
            return not self._boolean(self._unary(), "not")
        if self._match("op", "-"):
            value = self._unary()
            if not self._number(value):
                raise ExpressionError("unary '-' requires a number")
            return -value
        return self._primary()

    def _primary(self) -> Any:
        token = self._take()
        if token.kind == "number":
            return float(token.value) if "." in token.value else int(token.value)
        if token.kind == "string":
            try:
                return (
                    json.loads(token.value)
                    if token.value.startswith('"')
                    else _single_quote(token.value)
                )
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ExpressionError(f"invalid string literal: {error}") from error
        if token == _Token("op", "("):
            value = self._or()
            self._expect("op", ")")
            return value
        if token == _Token("op", "["):
            values: list[Any] = []
            if self._peek() != _Token("op", "]"):
                values.append(self._or())
                while self._match("op", ","):
                    values.append(self._or())
            self._expect("op", "]")
            return values
        if token.kind != "name":
            raise ExpressionError(f"unexpected token {token.value!r}")
        if token.value in {"true", "false", "null"}:
            return {"true": True, "false": False, "null": None}[token.value]
        if self._match("op", "("):
            args: list[Any] = []
            if self._peek() != _Token("op", ")"):
                args.append(self._or())
                while self._match("op", ","):
                    args.append(self._or())
            self._expect("op", ")")
            return _call(token.value, args, self.scope)
        value: Any = self._lookup(token.value)
        while self._match("op", "."):
            part = self._take()
            if part.kind != "name":
                raise ExpressionError("dotted reference segments must be names")
            value = _member(value, part.value)
        return value

    def _lookup(self, name: str) -> Any:
        if name not in self.scope:
            raise ExpressionError(f"unknown binding {name!r}")
        return self.scope[name]

    def _peek(self) -> _Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self) -> _Token:
        token = self._peek()
        if token is None:
            raise ExpressionError("unexpected end of expression")
        self.pos += 1
        return token

    def _match(self, kind: str, value: str) -> bool:
        if self._peek() == _Token(kind, value):
            self.pos += 1
            return True
        return False

    def _expect(self, kind: str, value: str) -> None:
        token = self._take()
        if token != _Token(kind, value):
            raise ExpressionError(f"expected {value!r}, got {token.value!r}")

    @staticmethod
    def _same_type(left: Any, right: Any, operation: str) -> None:
        numeric = Evaluator._number(left) and Evaluator._number(right)
        if not numeric and type(left) is not type(right):
            raise ExpressionError(f"{operation} operands must have the same type")

    @staticmethod
    def _number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def _boolean(value: Any, operation: str) -> bool:
        if not isinstance(value, bool):
            raise ExpressionError(f"{operation} requires boolean operands")
        return value


def evaluate(expression: str, scope: Mapping[str, Any]) -> Any:
    try:
        return Evaluator(expression, scope).parse()
    except (AgxError, KeyError, IndexError, TypeError, ValueError, ZeroDivisionError) as error:
        if isinstance(error, ExpressionError):
            raise
        raise ExpressionError(str(error)) from error


def interpolate(template: str, scope: Mapping[str, Any]) -> str:
    pattern = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
    return pattern.sub(lambda match: str(evaluate(match.group(1).strip(), scope)), template)


def _single_quote(value: str) -> str:
    return bytes(value[1:-1], "utf-8").decode("unicode_escape")


def _member(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        if name not in value:
            raise ExpressionError(f"object has no member {name!r}")
        return value[name]
    raise ExpressionError(f"cannot access member {name!r} on {type(value).__name__}")


def _call(name: str, args: list[Any], scope: Mapping[str, Any]) -> Any:
    functions: dict[str, Callable[..., Any]] = {
        "len": len,
        "count": len,
        "contains": lambda value, item: item in value,
        "startswith": lambda value, prefix: value.startswith(prefix),
        "endswith": lambda value, suffix: value.endswith(suffix),
        "lower": lambda value: value.lower(),
        "upper": lambda value: value.upper(),
        "trim": lambda value: value.strip(),
        "matches": lambda value, pattern: re.search(pattern, value) is not None,
        "split": lambda value, separator: value.split(separator),
        "join": lambda values, separator: separator.join(values),
        "int": int,
        "float": float,
        "bool": bool,
        "str": str,
        "json": json.loads,
        "get": lambda value, key, default=None: value.get(key, default),
        "default": lambda value, fallback: fallback if value is None else value,
        "any": lambda values: all(isinstance(value, bool) for value in values) and any(values),
        "all": lambda values: all(isinstance(value, bool) for value in values) and all(values),
        "succeeded": lambda node: _node_status(scope, node) == "succeeded",
        "failed": lambda node: _node_status(scope, node) in {"failed", "blocked"},
        "skipped": lambda node: _node_status(scope, node) == "skipped",
        "output": lambda node, key: _member(_member(_member(scope, "nodes"), node), "outputs")[key],
    }
    function = functions.get(name)
    if function is None:
        raise ExpressionError(f"unknown function {name!r}")
    try:
        return function(*args)
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ExpressionError(f"{name}() failed: {error}") from error


def _node_status(scope: Mapping[str, Any], node: str) -> str:
    return str(_member(_member(_member(scope, "nodes"), node), "status"))
