"""AsusRouter helpers module."""

from __future__ import annotations

import re
from typing import Any

from asusrouter.error import AsusRouterAccessError
from asusrouter.modules.endpoint.error import AccessError


def access_error_details(
    ex: BaseException,
) -> tuple[AccessError | None, dict[str, Any]]:
    """Return the access error code and attributes from an exception chain.

    The library wraps the login failure in a message-only exception, so
    the code and attributes live on the original cause.
    """

    seen: set[int] = set()
    current: BaseException | None = ex
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, AsusRouterAccessError):
            _message, code, attributes, *_rest = (*current.args, None, None)
            if isinstance(code, AccessError):
                return code, dict(attributes or {})
        current = current.__cause__ or current.__context__
    return None, {}


def clean_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Clean dictionary from None values."""

    return {
        k: v for k, v in raw.items() if v is not None or k.endswith("state")
    }


def flatten_dict(obj: Any, keystring: str = "", delimiter: str = "_"):
    """Flatten dictionary."""

    if isinstance(obj, dict):
        keystring = keystring + delimiter if keystring else keystring
        for key in obj:
            yield from flatten_dict(obj[key], keystring + str(key))
    else:
        yield keystring, obj


def as_dict(pyobj):
    """Return generator object as dictionary."""

    return dict(pyobj)


def list_from_dict(raw: dict[str, Any]) -> list[str]:
    """Return dictionary keys as list."""

    return list(raw.keys())


def to_unique_id(raw: str):
    """Convert string to unique_id."""

    string = (
        re.sub(r"(?<=[a-z0-9:_])(?=[A-Z])|[^a-zA-Z0-9:_]", " ", raw)
        .strip()
        .replace(" ", "_")
    )
    result = "".join(string.lower())
    while "__" in result:
        result = result.replace("__", "_")

    return result
