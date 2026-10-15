"""Conservative plain-text extraction from observed detail label/value markup.

No scripts, attributes, hidden controls, raw HTML, or unreviewed labels are stored.
Whitelisted formatting (``rich_text``) is derived from the same sanitized tree
and kept beside, never inside, the plain text and its content hash.
"""

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from waterlooworks_flash_next import rich_text
from waterlooworks_flash_next.rich_text import (
    FORMAT_VERSION,
    Block,
    ListBlock,
    Paragraph,
    safe_href,
    validate_blocks,
)

CONTENT_LABELS = frozenset(
    {
        "Work Term:",
        "Job Type:",
        "Job Title:",
        "Number of Job Openings:",
        "Level:",
        "Region:",
        "Job - Address Line One:",
        "Job - Address Line Two:",
        "Job - City:",
        "Job - Province/State:",
        "Job - Postal/Zip Code:",
        "Job - Country:",
        "Employment Location Arrangement:",
        "Additional Employment Arrangement Location Information:",
        "Work Term Duration:",
        "Special Job Requirements:",
        "Job Summary:",
        "Job Responsibilities:",
        "Required Skills:",
        "Compensation and Benefits:",
        "Targeted Degrees and Disciplines:",
        "Application Deadline",
        "Application Documents Required:",
        "Application Method:",
        "Organization:",
        "Division:",
        "Additional Application Information:",
        "Additional Information:",
        "Additional Job Identifiers:",
        "Employer Internal Job Number:",
        "Job Location (If Exact Address Unknown or Multiple Locations):",
        "Special Work Term Start/End Date Considerations:",
        "Transportation and Housing:",
    }
)
REQUIRED_LABELS = frozenset({"Job Title:", "Organization:", "Work Term:"})


class ContentError(Exception):
    """Fixed message only; never include response content."""


class StoredPostingContent(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, hide_input_in_errors=True, frozen=True
    )
    id: str = Field(pattern=r"^[0-9]+$")
    coverage: Literal["content_only"] = "content_only"
    fields: dict[str, str] = Field(repr=False)

    @field_validator("fields")
    @classmethod
    def reviewed_fields(cls, value: dict[str, str]) -> dict[str, str]:
        if not REQUIRED_LABELS <= value.keys() or not value.keys() <= CONTENT_LABELS:
            raise ValueError("Required or reviewed content labels do not match.")
        if not any(
            value.get(key)
            for key in ("Job Summary:", "Job Responsibilities:", "Required Skills:")
        ):
            raise ValueError("No verified descriptive content.")
        if any(not text.strip() or len(text) > 200_000 for text in value.values()):
            raise ValueError("Content field size is invalid.")
        return value


class StoredPostingFormatting(BaseModel):
    """Per-label sanitized formatting; labels without it show their plain text."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
        frozen=True,
        json_schema_serialization_defaults_required=True,
    )
    version: Literal["rich-v1"] = FORMAT_VERSION
    fields: dict[str, list[Block]] = Field(repr=False)

    @field_validator("fields")
    @classmethod
    def reviewed_fields(
        cls, value: dict[str, list[Paragraph | ListBlock]]
    ) -> dict[str, list[Paragraph | ListBlock]]:
        if not value.keys() <= CONTENT_LABELS:
            raise ValueError("Formatting labels do not match reviewed content.")
        for blocks in value.values():
            validate_blocks(blocks)
        return value


@dataclass
class _Node:
    tag: str
    children: list["_Node | str"] = field(default_factory=list)
    # Only a whitelisted absolute link target; every other attribute is dropped.
    href: str | None = None


def _text(node: _Node | str) -> str:
    if isinstance(node, str):
        return node
    value = "".join(_text(child) for child in node.children)
    return value + ("\n" if node.tag in {"br", "p", "li", "div", "tr"} else "")


def _targeted_list_text(node: _Node) -> str:
    """Only recorded list items inside this exact field's value container.

    The parser retains the observed collapsed widget's list, but never infers
    values from its heading or button.
    """

    def item_text(child: _Node | str) -> str:
        if isinstance(child, str):
            return child
        if child.tag == "button":
            return ""
        return "".join(item_text(part) for part in child.children) + (
            "\n" if child.tag in {"br", "p", "li", "div"} else ""
        )

    values: list[str] = []
    for child in node.children:
        if not isinstance(child, _Node) or child.tag == "button":
            continue
        if node.tag == "ul" and child.tag == "li":
            values.append(item_text(child))
        else:
            values.append(_targeted_list_text(child))
    return _clean("\n".join(values))


def _clean(value: str) -> str:
    return "\n".join(line.strip() for line in value.splitlines() if line.strip())


def _explicitly_hidden(
    attrs: list[tuple[str, str | None]], *, allow_display_none: bool = False
) -> bool:
    """Honor explicit source hiding, not computed/external stylesheet visibility."""
    attributes = dict(attrs)
    if (
        "hidden" in attributes
        or (attributes.get("aria-hidden") or "").strip().lower() == "true"
    ):
        return True
    style = re.sub(r"/\*.*?\*/", "", attributes.get("style") or "", flags=re.S)
    if "\\" in style:
        raise ContentError("Unsupported escaped inline visibility.")
    for declaration in style.split(";"):
        key, separator, raw = declaration.partition(":")
        if not separator:
            continue
        key = key.strip().lower()
        value = re.sub(r"\s*!important\s*$", "", raw.strip().lower())
        if key == "display":
            if value == "none":
                if allow_display_none:
                    continue
                return True
            if value not in {
                "block",
                "inline",
                "inline-block",
                "flex",
                "inline-flex",
                "grid",
                "inline-grid",
                "table",
                "inline-table",
                "table-row",
                "table-cell",
                "table-row-group",
                "table-header-group",
                "table-footer-group",
                "table-column",
                "table-column-group",
                "table-caption",
                "list-item",
                "contents",
                "flow-root",
            }:
                raise ContentError("Unsupported inline display semantics.")
        elif key in {"visibility", "content-visibility"}:
            if value in {"hidden", "collapse"}:
                return True
            if value != "visible":
                raise ContentError("Unsupported inline visibility semantics.")
    return False


class _ContentParser(HTMLParser):
    blocked = {
        "script",
        "style",
        "noscript",
        "iframe",
        "object",
        "header",
        "nav",
        "footer",
        "textarea",
        "select",
    }
    void = {
        "input",
        "img",
        "link",
        "meta",
        "embed",
        "source",
        "area",
        "base",
        "param",
        "wbr",
        "br",
        "hr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]
        self.suppressed: list[str] = []
        self.nodes = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes += 1
        if self.nodes > 50_000 or len(self.stack) + len(self.suppressed) > 100:
            raise ContentError("Detail structure exceeded bounds.")
        if (
            self.suppressed
            or tag in self.blocked
            or _explicitly_hidden(
                attrs,
                allow_display_none=tag == "ul" and self._observed_targeted_widget(),
            )
        ):
            if tag not in self.void:
                self.suppressed.append(tag)
            return
        node = _Node(tag)
        if tag == "a":
            node.href = safe_href(dict(attrs).get("href"))
        self.stack[-1].children.append(node)
        if tag not in self.void:
            self.stack.append(node)

    def _observed_targeted_widget(self) -> bool:
        """Bound the display:none exception to the observed field/widget siblings.

        Hidden ancestors never enter the stack. No attributes or control text
        are recovered from suppressed nodes to establish this association.
        """
        if len(self.stack) < 4:
            return False
        container, value, widget = self.stack[-3:]
        if (container.tag, value.tag, widget.tag) != ("div", "p", "div"):
            return False
        fields = [c for c in container.children if isinstance(c, _Node)]
        if (
            len(fields) != 2
            or fields[0].tag != "span"
            or fields[1] is not value
            or " ".join(_text(fields[0]).split()) != "Targeted Degrees and Disciplines:"
        ):
            return False
        headings = [c for c in widget.children if isinstance(c, _Node)]
        if len(headings) != 2 or headings[0].tag != "div":
            return False
        controls = [c for c in headings[0].children if isinstance(c, _Node)]
        return (
            len(controls) == 1
            and controls[0].tag == "strong"
            and " ".join(_text(controls[0]).split()) == "Targeted Clusters"
            and headings[1].tag == "button"
            and " ".join(_text(headings[1]).split())
            == "View Targeted Degrees and Disciplines"
        )

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.stack[-1].children.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.suppressed:
            for index in range(len(self.suppressed) - 1, -1, -1):
                if self.suppressed[index] == tag:
                    self.suppressed = self.suppressed[:index]
                    break
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def fields(self, node: _Node | None = None) -> dict[str, str]:
        node = self.root if node is None else node
        children = [child for child in node.children if isinstance(child, _Node)]
        result: dict[str, str] = {}
        if (
            node.tag == "div"
            and len(children) >= 2
            and children[0].tag == "span"
            and children[1].tag == "p"
        ):
            label = " ".join(_text(children[0]).split())
            value = (
                _targeted_list_text(children[1])
                if label == "Targeted Degrees and Disciplines:"
                else _clean(_text(children[1]))
            )
            if label in CONTENT_LABELS and value:
                result[label] = value
        for child in children:
            for label, value in self.fields(child).items():
                if label in result and result[label] != value:
                    raise ContentError("Ambiguous duplicate detail field.")
                result[label] = value
        return result

    def value_nodes(self, node: _Node | None = None) -> dict[str, _Node]:
        """First value node per label, mirroring ``fields`` structure detection."""
        node = self.root if node is None else node
        children = [child for child in node.children if isinstance(child, _Node)]
        result: dict[str, _Node] = {}
        if (
            node.tag == "div"
            and len(children) >= 2
            and children[0].tag == "span"
            and children[1].tag == "p"
        ):
            result[" ".join(_text(children[0]).split())] = children[1]
        for child in children:
            for label, value in self.value_nodes(child).items():
                result.setdefault(label, value)
        return result

    def formatting(
        self, fields: dict[str, str], secrets: tuple[str, ...]
    ) -> StoredPostingFormatting:
        """Never raises: a field whose formatting fails shows its plain text."""
        try:
            nodes = self.value_nodes()
            formatted: dict[str, list[Paragraph | ListBlock]] = {}
            for label, plain in fields.items():
                node = nodes.get(label)
                if node is None or label == "Targeted Degrees and Disciplines:":
                    continue
                try:
                    blocks = rich_text.build_blocks(node, plain, secrets)
                except Exception:
                    blocks = None
                if blocks is not None:
                    formatted[label] = blocks
            return StoredPostingFormatting(fields=formatted)
        except Exception:
            return StoredPostingFormatting(fields={})


def extract_content(
    markup: str,
    posting_id: str,
    title: str,
    organization: str | None,
    *,
    secrets: tuple[str, ...] = (),
) -> StoredPostingContent:
    """Plain-text content only; see ``extract_content_with_formatting``."""
    return extract_content_with_formatting(
        markup, posting_id, title, organization, secrets=secrets
    )[0]


def extract_content_with_formatting(
    markup: str,
    posting_id: str,
    title: str,
    organization: str | None,
    *,
    secrets: tuple[str, ...] = (),
) -> tuple[StoredPostingContent, StoredPostingFormatting]:
    """Request-bound identity; verify text title/org, never infer metadata.

    Explicit hiding is excluded except the observed targeted widget's inline
    display:none list. External/class CSS
    visibility is not computed, so this is not a browser-visible-text guarantee.
    """
    try:
        parser = _ContentParser()
        parser.feed(markup)
        parser.close()
        fields = parser.fields()
        if " ".join(fields.get("Job Title:", "").split()) != " ".join(
            unescape(title).split()
        ):
            raise ContentError("Detail title did not match the requested posting.")
        if organization and " ".join(
            fields.get("Organization:", "").split()
        ) != " ".join(unescape(organization).split()):
            raise ContentError(
                "Detail organization did not match the requested posting."
            )
        if any(
            secret in value
            for secret in secrets
            if len(secret) >= 6
            for value in fields.values()
        ):
            raise ContentError("Session-bearing content was withheld.")
        content = StoredPostingContent(id=posting_id, fields=fields)
        return content, parser.formatting(fields, secrets)
    except Exception:
        raise ContentError(
            "Detail content validation failed; diagnostics suppressed."
        ) from None
