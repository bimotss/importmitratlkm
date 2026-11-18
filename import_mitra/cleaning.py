"""Formula repairs for the explicit missing-source policy selected by the owner."""
import re

ERROR_PATTERN = r"^#(REF!|N/A|VALUE!|DIV/0!|NAME\?|NUM!|ERROR!|SPILL!|CALC!|NULL!)(\s*\(|$)"


def formula_separator(formula, default=","):
    stack, quote = [], None
    for char in formula:
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
        elif char in "({":
            stack.append(char)
        elif char in ")}":
            if stack:
                stack.pop()
        elif char in ";," and stack and stack[-1] == "(":
            return char
    return default


def protected_formula(formula, message="", default_separator=","):
    """Keep valid results and future recalculation; blank only unavailable outputs.

    Unsupported AI and explicit error constants cannot recover automatically.
    The caller backs up their original expressions before clearing them.
    """
    expression = formula.strip().lstrip("=").strip()
    if (re.match(r"^(NA|GEMINI|AI)\s*\(", expression, re.IGNORECASE)
            or "formula parse error" in message.lower()):
        return None
    if formula.startswith(("=LET(_im_clean;", "=LET(_im_clean,")):
        return formula
    sep = formula_separator(formula, default_separator)
    # ARRAYFORMULA preserves individual valid values and the header of a spill.
    return (f'=LET(_im_clean{sep}IFERROR(({expression}){sep}""){sep}'
            f'ARRAYFORMULA(IF(REGEXMATCH(TO_TEXT(_im_clean){sep}"{ERROR_PATTERN}"){sep}""{sep}_im_clean)))')
