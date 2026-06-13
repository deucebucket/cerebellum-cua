"""AT-SPI role-name -> canonical ControlType taxonomy mapping.

AT-SPI2 exposes roles as lowercase human-readable strings via
``Accessible.get_role_name()`` (e.g. "push button", "check box", "page tab").
Cerebellum CUA's canonical taxonomy is :class:`cerebellum_cua.model.ControlType` (UIA-derived
integers), so every backend maps its native roles into THOSE ints to keep the
matrix, predicates, and semantics uniform across OSes.

This module is pure data + a couple of pure helpers; it imports nothing from
``gi``/``Atspi`` so it is trivially unit-testable on any host.
"""

from __future__ import annotations

from cerebellum_cua.model import ControlType

_BUTTON = int(ControlType.BUTTON)
_CHECK = int(ControlType.CHECK_BOX)
_RADIO = int(ControlType.RADIO_BUTTON)
_EDIT = int(ControlType.EDIT)
_MENU = int(ControlType.MENU)
_MENU_BAR = int(ControlType.MENU_BAR)
_MENU_ITEM = int(ControlType.MENU_ITEM)
_LIST = int(ControlType.LIST)
_LIST_ITEM = int(ControlType.LIST_ITEM)
_TAB = int(ControlType.TAB)
_TAB_ITEM = int(ControlType.TAB_ITEM)
_WINDOW = int(ControlType.WINDOW)
_PANE = int(ControlType.PANE)
_TREE = int(ControlType.TREE)
_TREE_ITEM = int(ControlType.TREE_ITEM)
_TABLE = int(ControlType.TABLE)
_DATA_ITEM = int(ControlType.DATA_ITEM)
_DATA_GRID = int(ControlType.DATA_GRID)
_TEXT = int(ControlType.TEXT)
_HYPERLINK = int(ControlType.HYPERLINK)
_COMBO = int(ControlType.COMBO_BOX)
_SCROLL_BAR = int(ControlType.SCROLL_BAR)
_SEPARATOR = int(ControlType.SEPARATOR)
_TOOL_BAR = int(ControlType.TOOL_BAR)
_DOCUMENT = int(ControlType.DOCUMENT)
_IMAGE = int(ControlType.IMAGE)
_SLIDER = int(ControlType.SLIDER)
_SPINNER = int(ControlType.SPINNER)
_PROGRESS = int(ControlType.PROGRESS_BAR)
_STATUS = int(ControlType.STATUS_BAR)
_TITLE_BAR = int(ControlType.TITLE_BAR)
_TOOL_TIP = int(ControlType.TOOL_TIP)
_GROUP = int(ControlType.GROUP)
_HEADER = int(ControlType.HEADER)
_HEADER_ITEM = int(ControlType.HEADER_ITEM)
_CALENDAR = int(ControlType.CALENDAR)
_SPLIT_BUTTON = int(ControlType.SPLIT_BUTTON)
_CUSTOM = int(ControlType.CUSTOM)

#: AT-SPI role-name (lowercase, as returned by get_role_name) -> ControlType int.
ATSPI_ROLE_TO_CONTROL_TYPE: dict[str, int] = {
    # Buttons / activation.
    "push button": _BUTTON,
    "button": _BUTTON,
    "toggle button": _BUTTON,
    "split button": _SPLIT_BUTTON,
    "check box": _CHECK,
    "check menu item": _MENU_ITEM,
    "radio button": _RADIO,
    "radio menu item": _MENU_ITEM,
    # Text / entry.
    "entry": _EDIT,
    "text": _EDIT,
    "password text": _EDIT,
    "paragraph": _TEXT,
    "label": _TEXT,
    "static": _TEXT,
    "caption": _TEXT,
    "heading": _TEXT,
    "section": _GROUP,
    # Menus.
    "menu": _MENU,
    "menu bar": _MENU_BAR,
    "menu item": _MENU_ITEM,
    "popup menu": _MENU,
    "tear off menu item": _MENU_ITEM,
    # Lists.
    "list": _LIST,
    "list box": _LIST,
    "list item": _LIST_ITEM,
    # Tabs.
    "page tab": _TAB_ITEM,
    "page tab list": _TAB,
    # Windows / containers.
    "frame": _WINDOW,
    "window": _WINDOW,
    "dialog": _WINDOW,
    "alert": _WINDOW,
    "file chooser": _WINDOW,
    "color chooser": _WINDOW,
    "panel": _PANE,
    "filler": _PANE,
    "viewport": _PANE,
    "scroll pane": _PANE,
    "split pane": _PANE,
    "layered pane": _PANE,
    "root pane": _PANE,
    "internal frame": _PANE,
    "redundant object": _PANE,
    "application": _PANE,
    "grouping": _GROUP,
    # Trees.
    "tree": _TREE,
    "tree table": _TREE,
    "tree item": _TREE_ITEM,
    # Tables.
    "table": _TABLE,
    "table cell": _DATA_ITEM,
    "table row": _DATA_ITEM,
    "table column header": _HEADER_ITEM,
    "table row header": _HEADER_ITEM,
    "column header": _HEADER_ITEM,
    "row header": _HEADER_ITEM,
    # Links / media.
    "link": _HYPERLINK,
    "image": _IMAGE,
    "icon": _IMAGE,
    "canvas": _IMAGE,
    # Composite widgets.
    "combo box": _COMBO,
    "scroll bar": _SCROLL_BAR,
    "slider": _SLIDER,
    "spin button": _SPINNER,
    "progress bar": _PROGRESS,
    "level bar": _PROGRESS,
    "separator": _SEPARATOR,
    "tool bar": _TOOL_BAR,
    "tool tip": _TOOL_TIP,
    "status bar": _STATUS,
    "calendar": _CALENDAR,
    "date editor": _CALENDAR,
    # Documents.
    "document frame": _DOCUMENT,
    "document web": _DOCUMENT,
    "document text": _DOCUMENT,
    "document spreadsheet": _DOCUMENT,
    "document presentation": _DOCUMENT,
    "article": _DOCUMENT,
    "header": _HEADER,
    "footer": _HEADER,
}

# State-name constants we care about for interactivity hints (string-safe so
# _convert can pass the names it derived from the AT-SPI state set).
_ACTIONABLE_INTERFACES = frozenset({"Action", "EditableText"})


def control_type_for(role_name: str | None) -> int:
    """Map an AT-SPI role name to a canonical ControlType int (CUSTOM fallback)."""
    if not role_name:
        return _CUSTOM
    return ATSPI_ROLE_TO_CONTROL_TYPE.get(role_name.strip().lower(), _CUSTOM)


def interactive_type_for(role_name: str | None, interfaces: set[str]) -> str:
    """Classify the *kind* of interaction an element offers.

    Returns one of: ``"editable"`` (text entry), ``"actionable"`` (clickable /
    invokable via the Action interface), or ``""`` (no direct interaction).
    Used by the predicate / convert layer to flag interactive surface without
    re-querying the live element.
    """
    ifaces = interfaces or set()
    if "EditableText" in ifaces:
        return "editable"
    role = (role_name or "").strip().lower()
    if role in {"entry", "text", "password text"} and "Text" in ifaces:
        return "editable"
    if "Action" in ifaces:
        return "actionable"
    return ""
