"""Reusable natural ordering for entry codes and other labels."""

import re


def natural_sort_key(value):
    """Compare digit runs numerically, ignore case, and place empty values last.

    Equal keys retain their input order when used with Python's stable sort.
    Token tags keep mixed numeric and text prefixes comparable.
    """
    return (
        not value,
        tuple(
            (0, int(part)) if part.isdecimal() else (1, part.casefold())
            for part in re.findall(r"\d+|\D+", value)
        ),
    )
