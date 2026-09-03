# Target System Code Syntax (New Mode / With Thermodynamic Functions)

This document describes the syntax of expressions **after** processing by the new-mode `prepareCodeForSystem` transformer. This mode is activated when the original internal code contains at least one of the thermodynamic functions: `ENTHALPY_PS`, `ENTHALPY_PT`, `PRESSURE_SATURATION`, `TEMPERATURE_SATURATION`, `ENTROPY_PT`, `TEMPERATURE_PS`.

## General Rules

- Expressions consist of **KKS‑codes wrapped in curly braces**, numeric constants, operators, and function calls.
- KKS‑codes **must** be enclosed in curly braces: `{21AAA10CT801__XQ10}`, `{GT1_CFA10CE001_XE01}`.  
  Inside the braces, the full original KKS code is written **without** the `P` prefix, even if it starts with a digit.
- The special character `§` is replaced by underscore `_`.
- Function names are case‑insensitive in the source, but in the target code they are written in **lowercase**.
- Spaces are allowed around operators and after commas.

## Operators

| Operator | Meaning | Example |
|----------|---------|----------|
| `+` `-` `*` `/` `**` `%` | Arithmetic | `{A} + {B} * 2`, `{A} ** 2` |
| `>` `<` `>=` `<=` `==` `!=` | Comparison | `{A} > 0`, `{B} != 0` |
| `and` `or` `not` | Logical (AND, OR, NOT) | `({A} > 0) and ({B} < 10)`, `not ({C} == 0)` |

- Logical operators are written as lowercase `and`, `or`, `not`.
- Comparison for equality is `==`.

## Numeric Constants

- Integer or decimal numbers, e.g., `0`, `-1`, `3.14`, `1.5e-3`.
- Scientific notation is allowed.
- Unary minus is used as usual: `-1.0`, `-{A}`.

## Conditional Expression
when(condition, value_if_true, value_if_false)

text
- Returns `value_if_true` if `condition` is true (non‑zero), otherwise `value_if_false`.
- The function name is lowercase `when`.
- All arguments are evaluated element‑wise over the time series.

## Math Functions

| Function | Description |
|----------|-------------|
| `abs(x)` | Absolute value |
| `exp(x)` | Exponential e^x |
| `pow(x, y)` | Power x^y |
| `log(x)` | Natural logarithm (x > 0) |
| `log10(x)` | Base‑10 logarithm (x > 0) |
| `min(a, b, ...)` | Minimum of arguments |
| `max(a, b, ...)` | Maximum of arguments |
| `round(x, n)` | Round x to n decimal places (n defaults to 0) |

## Statistical Functions

These functions accept a variable number of arguments.

| Function | Description |
|----------|-------------|
| `mean(a, b, c, ...)` or `avg(a, b, ...)` | Arithmetic mean |
| `median(x, y, z, ...)` or `med(x, y, ...)` | Median |
| `min(a, b, c, ...)` | Minimum |
| `max(a, b, c, ...)` | Maximum |
| `variance(a, b, c, ...)` | Variance (requires at least 2 arguments) |
| `stdev(a, b, c, ...)` | Standard deviation (requires at least 2 arguments) |

## History / Rolling Functions

These functions operate on past values of a signal within a specified time window (in minutes).  
Signal arguments are given **inside curly braces** (without additional quotes and without the `P` prefix).

| Function | Description |
|----------|-------------|
| `history_avg({a}, N)` or `historyavg({a}, N)` | Average over last N minutes |
| `history_min({a}, N)` or `historymin({a}, N)` | Minimum over last N minutes |
| `history_max({a}, N)` or `historymax({a}, N)` | Maximum over last N minutes |
| `history_diff({a}, N)` or `historydiff({a}, N)` | Difference between current value and value N minutes ago |
| `history_diff_max({a}, N)` or `historydiffmax({a}, N)` | Difference between max and min over last N minutes |

- `{a}` is a KKS‑code in curly braces.
- `N` is an integer number of minutes.

Examples:
{P10MAA01} + history_avg({P10MAA02}, 60)
history_max({10MAA01}, 10) - history_min({10MAA02}, 20)

text

## Previous Value
prev({a})

text
- Returns the previous value of the signal.
- The signal argument is in curly braces (no quotes, no `P` prefix).

## Table Interpolation Functions

### 1D Interpolation
getpoint("table_name", X_value, Y_value, axis_to_find)

text
- Finds `Y` given `X` (or vice versa) by linear interpolation from a 2‑column table.
- `table_name`: string literal identifying the table (kept in quotes).
- `axis_to_find`: `"Y"` to get Y for a given X, or `"X"` to get X for a given Y.

### Multi‑dimensional Interpolation
interpolate("table_name", "target_column", value1, value2, ...)

text
- Performs KNN‑based interpolation using a table with multiple feature columns.
- `table_name` and `target_column` are string literals (kept in quotes).
- Other arguments follow the same KKS‑in‑braces rule.

## Thermodynamic Functions

These functions are available **only** in this mode and are written in lowercase.

| Function | Description |
|----------|-------------|
| `enthalpy_ps(p, s)` | Enthalpy of superheated steam by pressure and entropy |
| `enthalpy_pt(p, t)` | Enthalpy of superheated steam by pressure and temperature |
| `pressure_saturation(t)` | Saturation pressure by temperature |
| `temperature_saturation(p)` | Saturation temperature by pressure |
| `entropy_pt(p, t)` | Entropy of superheated steam by pressure and temperature |
| `temperature_ps(p, s)` | Temperature of superheated steam by pressure and entropy |

- Arguments are ordinary expressions (which may include KKS‑codes in curly braces, numbers, arithmetic, and nested functions).
- Example: `enthalpy_pt({10MAA50FP001_P_IN} / 10, {10MAA50FT001_t_IN})`

## Notes for LLM

- This is the **target system code** after new-mode transformation.
- KKS‑codes are **always** in curly braces and **never** have the `P` prefix inside the braces.
- Function names are lowercase.
- Logical operators are `and`, `or`, `not`.
- No quotes around signal arguments in history, previous, or interpolation functions; they are already represented by curly braces.
- The `§` character is replaced by `_`.