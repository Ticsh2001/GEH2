# Target System Code Syntax (Old Mode)

## General Rules

- Expressions consist of **signal names**, **numeric constants**, **operators**, and **function calls**.
- Signal names are written **with a `P` prefix** if the original KKS code starts with a digit. Example: original `10MAA01` → `P10MAA01`.  
  Signals starting with a letter remain unchanged.
- The special character `§` is replaced by underscore `_` everywhere.
- Spaces are allowed around operators and after commas.
- String literals for table names or signal references inside certain functions are enclosed in single quotes `'...'` or double quotes `"..."`.

## Operators

| Operator | Meaning | Example |
|----------|---------|----------|
| `+` `-` `*` `/` | Arithmetic | `A + B * 2` |
| `>` `<` `>=` `<=` `==` `!=` | Comparison | `A > 100`, `B == 0` |
| `&&` `\|\|` `!` | Logical (AND, OR, NOT) | `(A > 0) && (B < 10)`, `!(C == 0)` |

- Logical operators are written as `&&`, `||`, `!`.
- Comparison for equality is `==` (not single `=`).

## Numeric Constants

- Integer or decimal numbers, e.g., `0`, `-1`, `3.14`, `1.5e-3`.
- Scientific notation is allowed.
- Unary minus before a signal that starts with `P` and a digit is converted to `-1 * ...`.  
  Example: `-P10MAA01` → `-1 * P10MAA01`.  
  Unary minus before parentheses is also converted: `-(A + B)` → `-1 * (A + B)`.

## Conditional Expression
WHEN(condition, value_if_true, value_if_false)

text
- Returns `value_if_true` if `condition` is true (non-zero), otherwise `value_if_false`.
- All arguments are evaluated element-wise over the time series.

## Math Functions

| Function | Description |
|----------|-------------|
| `ABS(x)` | Absolute value |
| `EXP(x)` | Exponential e^x |
| `POW(x, y)` | Power x^y |
| `LOG(x)` | Natural logarithm (x > 0) |
| `LOG10(x)` | Base-10 logarithm (x > 0) |
| `MIN(a, b, ...)` | Minimum of arguments |
| `MAX(a, b, ...)` | Maximum of arguments |
| `AVG(a, b, ...)` | Average of arguments |
| `MED(a, b, ...)` | Median of arguments |
| `ROUND(x, n)` | Round x to n decimal places (n defaults to 0) |

Function names are case-insensitive in the source code and are usually written in uppercase.

## History / Rolling Functions

These functions operate on past values of a signal within a specified time window (in minutes).

| Function | Description |
|----------|-------------|
| `HISTORYAVG(signal, period)` | Arithmetic mean over the window |
| `HISTORYSUM(signal, period)` | Sum over the window |
| `HISTORYCOUNT(signal, period)` | Count of non‑NaN values in the window |
| `HISTORYMAX(signal, period)` | Maximum value in the window |
| `HISTORYMIN(signal, period)` | Minimum value in the window |
| `HISTORYDIFF(signal, period)` | Difference between max and min in the window |
| `HISTORYGRADIENT(signal, period)` | Slope of linear regression over the window (value per minute) |

- `signal`: a signal name **must be enclosed in quotes** (single or double).  
  **Important:** Inside the quotes, the `P` prefix is **removed**.  
  Example: if the original signal starts with a digit, e.g., `10MAA01`, then in the target code it appears as `'10MAA01'`.  
  If it starts with a letter, it appears as `'KKS_TAG'`.
- `period`: integer number of minutes (e.g., `60` for one hour).

## Previous Value
PREV(signal)

text
- Returns the previous value of the signal.
- Same quoting and `P`‑prefix removal rules as for `HISTORY*` functions.  
  Example: `PREV('10MAA01')`, `PREV('KKS_TAG')`.

## Table Interpolation Functions

### 1D Interpolation
GETPOINT("table_name", X_value, Y_value, axis_to_find)

text
- Finds `Y` given `X` (or vice versa) by linear interpolation from a 2‑column table.
- `table_name`: string literal identifying the table.
- `axis_to_find`: `"Y"` to get Y for a given X, or `"X"` to get X for a given Y.
- The first argument (table name) is **always quoted**; the signal arguments are quoted and have `P` removed.

### Multi‑dimensional Interpolation
INTERPOLATE("table_name", "target_column", value1, value2, ...)

text
- Performs KNN‑based interpolation using a table with multiple feature columns.
- `table_name`: string literal.
- `target_column`: name of the column whose value is being interpolated (string).
- `value1, value2, ...`: input values for each feature column (order must match the table columns except the target column).

## Notes for LLM

- This is the **target system code**, not the internal editor representation.  
- Signal names that start with a digit have the prefix `P` added in the target code, **except** when they are passed as quoted arguments to `HISTORY*`, `PREV`, `GETPOINT`, or `INTERPOLATE` – there the `P` prefix is removed.
- The `§` character is always replaced by `_`.
- Logical operators use `&&`, `||`, `!`.
- Equality comparison uses `==`.
- Unary minus before a digit‑prefixed signal or before parentheses is converted to `-1 * ...`.