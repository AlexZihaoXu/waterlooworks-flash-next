"""Whitelisted, bounded description formatting stored beside the plain text.

The model is a small document tree, never HTML: paragraphs and (un)ordered lists
of blocks; inline text runs that may be bold and/or italic; links whose target
is an absolute http(s) URL or a plain ``mailto:`` address; and line breaks.
Everything else in the source markup is reduced to its text (or dropped when
the plain-text parser already drops it: scripts, hidden controls, attributes).

The builder walks the same sanitized parse tree as the plain-text extractor and
only keeps a field when its characters, ignoring whitespace, are exactly the
plain text's, so formatting can never show words the saved text lacks. Any
failure or bound overrun drops that field's formatting; plain text still saves.
"""

import re
from collections.abc import Sequence
from typing import Annotated, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

FORMAT_VERSION: Literal["rich-v1"] = "rich-v1"
MAX_HREF_LENGTH = 2048
MAX_LIST_DEPTH = 4
MAX_FIELD_NODES = 5000
MAX_TEXT_LENGTH = 200_000
_MAILTO = re.compile(r"mailto:[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}")
_ASCII_SPACE = re.compile(r"[ \t\n\r\f]+")


def safe_href(value: str | None) -> str | None:
    """The href itself when it is a safe absolute link, else None (text only).

    Allowed: ``http``/``https`` URLs with a host and no user info, and plain
    ``mailto:`` addresses without headers. Whitespace, control characters and
    backslashes anywhere reject the link instead of being repaired.
    """
    if not value or len(value) > MAX_HREF_LENGTH:
        return None
    if any(ord(char) <= 0x20 or ord(char) == 0x7F or char == "\\" for char in value):
        return None
    if _MAILTO.fullmatch(value):
        return value
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        if not value[len(parts.scheme) :].startswith("://"):
            return None
        parts.port  # noqa: B018 - raises ValueError for an invalid port
    except ValueError:
        return None
    return value


class _Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
        frozen=True,
        json_schema_serialization_defaults_required=True,
    )


class TextRun(_Contract):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    bold: bool = False
    italic: bool = False


class LinkRun(_Contract):
    """Opens in a new tab with rel="noopener noreferrer" when rendered."""

    type: Literal["link"]
    href: str = Field(max_length=MAX_HREF_LENGTH)
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    bold: bool = False
    italic: bool = False

    @field_validator("href")
    @classmethod
    def safe(cls, value: str) -> str:
        if safe_href(value) != value:
            raise ValueError("Unsafe link target.")
        return value


class LineBreak(_Contract):
    type: Literal["break"]


Inline = Annotated[TextRun | LinkRun | LineBreak, Field(discriminator="type")]


class Paragraph(_Contract):
    type: Literal["paragraph"]
    content: list[Inline] = Field(min_length=1, max_length=MAX_FIELD_NODES)


class ListBlock(_Contract):
    """Each item is a non-empty sequence of blocks (paragraphs or nested lists)."""

    type: Literal["list"]
    ordered: bool
    items: list[list["Block"]] = Field(min_length=1, max_length=MAX_FIELD_NODES)

    @field_validator("items")
    @classmethod
    def non_empty_items(cls, value: list[list["Block"]]) -> list[list["Block"]]:
        if any(not item for item in value):
            raise ValueError("List items must not be empty.")
        return value


Block = Annotated[Paragraph | ListBlock, Field(discriminator="type")]
ListBlock.model_rebuild()


def validate_blocks(blocks: Sequence[Paragraph | ListBlock]) -> None:
    """Bound depth and size of an already-typed block sequence."""
    count = 0

    def visit(items: Sequence[Paragraph | ListBlock], depth: int) -> None:
        nonlocal count
        for block in items:
            count += 1
            if isinstance(block, Paragraph):
                count += len(block.content)
            else:
                if depth >= MAX_LIST_DEPTH:
                    raise ValueError("List nesting exceeds the bound.")
                for item in block.items:
                    visit(item, depth + 1)
            if count > MAX_FIELD_NODES:
                raise ValueError("Formatting exceeds the size bound.")

    if not blocks:
        raise ValueError("Formatting needs at least one block.")
    visit(blocks, 0)


def blocks_text(blocks: Sequence[Paragraph | ListBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, Paragraph):
            parts.extend(
                run.text for run in block.content if not isinstance(run, LineBreak)
            )
        else:
            for item in block.items:
                parts.append(blocks_text(item))
    return "".join(parts)


def same_characters(plain: str, blocks: Sequence[Paragraph | ListBlock]) -> bool:
    """Formatting shows exactly the plain text's characters, ignoring whitespace."""
    return "".join(plain.split()) == "".join(blocks_text(blocks).split())


def is_trivial(blocks: Sequence[Paragraph | ListBlock]) -> bool:
    """One unmarked paragraph without breaks or links renders like plain text."""
    return (
        len(blocks) == 1
        and isinstance(blocks[0], Paragraph)
        and all(
            isinstance(run, TextRun) and not run.bold and not run.italic
            for run in blocks[0].content
        )
    )


class SourceNode(Protocol):
    """The plain-text parser's sanitized node: tag, children and a safe href."""

    @property
    def tag(self) -> str: ...

    @property
    def children(self) -> Sequence["SourceNode | str"]: ...

    @property
    def href(self) -> str | None: ...


_MARKS = {"strong": "bold", "b": "bold", "em": "italic", "i": "italic"}
_BLOCKS = frozenset(
    {
        "p",
        "div",
        "li",
        "tr",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "blockquote",
        "section",
        "article",
        "aside",
        "dl",
        "dt",
        "dd",
        "pre",
        "address",
        "figure",
        "figcaption",
        "center",
        "main",
    }
)
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


class _Bound(Exception):
    pass


class _Builder:
    def __init__(self, secrets: tuple[str, ...]) -> None:
        self.secrets = tuple(secret for secret in secrets if len(secret) >= 6)
        self.root: list[Paragraph | ListBlock] = []
        self.container = self.root
        self.inline: list[TextRun | LinkRun | LineBreak] = []
        self.space = True
        self.nodes = 0

    def _count(self, amount: int = 1) -> None:
        self.nodes += amount
        if self.nodes > MAX_FIELD_NODES:
            raise _Bound

    def _trim_end(self) -> None:
        while self.inline and isinstance(self.inline[-1], TextRun | LinkRun):
            last = self.inline[-1]
            stripped = last.text.rstrip(" ")
            if stripped == last.text:
                return
            self.inline.pop()
            if stripped:
                self.inline.append(last.model_copy(update={"text": stripped}))
                return

    def flush(self) -> None:
        self._trim_end()
        while self.inline and isinstance(self.inline[-1], LineBreak):
            self.inline.pop()
            self._trim_end()
        if any(
            isinstance(run, TextRun | LinkRun) and run.text.strip()
            for run in self.inline
        ):
            self._count()
            self.container.append(Paragraph(type="paragraph", content=self.inline))
        self.inline = []
        self.space = True

    def text(self, value: str, bold: bool, italic: bool, href: str | None) -> None:
        value = _ASCII_SPACE.sub(" ", value)
        if self.space:
            value = value.lstrip(" ")
        if not value:
            return
        self.space = value.endswith(" ")
        if href is not None and any(secret in href for secret in self.secrets):
            href = None
        last = self.inline[-1] if self.inline else None
        if (
            isinstance(last, TextRun | LinkRun)
            and last.bold == bold
            and last.italic == italic
            and (last.href if isinstance(last, LinkRun) else None) == href
        ):
            self.inline[-1] = last.model_copy(update={"text": last.text + value})
            return
        self._count()
        self.inline.append(
            TextRun(type="text", text=value, bold=bold, italic=italic)
            if href is None
            else LinkRun(type="link", href=href, text=value, bold=bold, italic=italic)
        )

    def line_break(self) -> None:
        self._trim_end()
        self.space = True
        if not self.inline:
            return
        if isinstance(self.inline[-1], LineBreak):
            self.inline.pop()
            self.flush()
            return
        self._count()
        self.inline.append(LineBreak(type="break"))

    def walk(
        self,
        node: SourceNode | str,
        *,
        bold: bool,
        italic: bool,
        href: str | None,
        depth: int,
    ) -> None:
        if isinstance(node, str):
            self.text(node, bold, italic, href)
            return
        tag = node.tag
        if tag == "br":
            self.line_break()
        elif tag in ("ul", "ol"):
            self._list(node, bold=bold, italic=italic, href=href, depth=depth)
        elif tag in _BLOCKS or tag in _HEADINGS:
            self.flush()
            for child in node.children:
                self.walk(
                    child,
                    bold=bold or tag in _HEADINGS,
                    italic=italic,
                    href=href,
                    depth=depth,
                )
            self.flush()
        else:
            mark = _MARKS.get(tag)
            link = node.href if tag == "a" else href
            for child in node.children:
                self.walk(
                    child,
                    bold=bold or mark == "bold",
                    italic=italic or mark == "italic",
                    href=link,
                    depth=depth,
                )

    def _list(
        self,
        node: SourceNode,
        *,
        bold: bool,
        italic: bool,
        href: str | None,
        depth: int,
    ) -> None:
        if depth >= MAX_LIST_DEPTH:
            raise _Bound
        self.flush()
        outer = self.container
        items: list[list[Paragraph | ListBlock]] = []
        current: list[Paragraph | ListBlock] | None = None
        try:
            for child in node.children:
                if isinstance(child, str) and not child.strip():
                    continue
                is_item = not isinstance(child, str) and child.tag == "li"
                if is_item or current is None:
                    current = []
                    items.append(current)
                self.container = current
                if is_item:
                    assert not isinstance(child, str)
                    for part in child.children:
                        self.walk(
                            part, bold=bold, italic=italic, href=href, depth=depth + 1
                        )
                else:
                    self.walk(
                        child, bold=bold, italic=italic, href=href, depth=depth + 1
                    )
                self.flush()
        finally:
            self.container = outer
        kept = [item for item in items if item]
        if kept:
            self._count()
            outer.append(ListBlock(type="list", ordered=node.tag == "ol", items=kept))


def build_blocks(
    node: SourceNode, plain: str, secrets: tuple[str, ...] = ()
) -> list[Paragraph | ListBlock] | None:
    """Formatting for one field's value node, or None to show plain text only."""
    builder = _Builder(secrets)
    try:
        builder.walk(node, bold=False, italic=False, href=None, depth=0)
        builder.flush()
    except _Bound:
        return None
    blocks = builder.root
    if not blocks or is_trivial(blocks) or not same_characters(plain, blocks):
        return None
    if builder.secrets and any(
        secret in blocks_text(blocks) for secret in builder.secrets
    ):
        return None
    validate_blocks(blocks)
    return blocks
