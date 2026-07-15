from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True, slots=True)
class LoginForm:
    action: str
    fields: dict[str, str]
    values_by_id: dict[str, str]


class _LoginFormParser(HTMLParser):
    def __init__(self, target_form_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self._target_form_id = target_form_id
        self._in_target = False
        self.action = ""
        self.fields: dict[str, str] = {}
        self.values_by_id: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "form" and attributes.get("id") == self._target_form_id:
            self._in_target = True
            self.action = attributes.get("action", "")
            return

        if not self._in_target or tag != "input":
            return

        value = attributes.get("value", "")
        if name := attributes.get("name"):
            self.fields[name] = value
        if element_id := attributes.get("id"):
            self.values_by_id[element_id] = value

    def handle_endtag(self, tag: str) -> None:
        if self._in_target and tag == "form":
            self._in_target = False


class _ElementTextParser(HTMLParser):
    def __init__(self, target_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self._target_id = target_id
        self._depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if self._depth:
            self._depth += 1
        elif attributes.get("id") == self._target_id:
            self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth and data.strip():
            self._parts.append(data.strip())

    @property
    def text(self) -> str:
        return " ".join(self._parts)


def parse_login_form(html: str, form_id: str = "pwdFromId") -> LoginForm:
    parser = _LoginFormParser(form_id)
    parser.feed(html)
    return LoginForm(
        action=parser.action,
        fields=parser.fields,
        values_by_id=parser.values_by_id,
    )


def extract_element_text(html: str, element_id: str) -> str:
    parser = _ElementTextParser(element_id)
    parser.feed(html)
    return parser.text
