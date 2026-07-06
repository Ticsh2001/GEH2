# JSON Dependency Tree Structure

This JSON describes a project and all its input signals as a recursive tree.

## Top Level (Root)

| Field | Type | Description |
|-------|------|-------------|
| `project` | string | KKS code of the current project |
| `type` | string | Project type: `"parameter"`, `"rule"`, or `"template"` |
| `description` | string | Project description (from metadata) |
| `dimension` | string | Dimension (only for `parameter`) |
| `possibleCause` | string | Possible cause (only for `rule`) |
| `guidelines` | string | Guidelines (only for `rule`) |
| `code` | string | Final code of the project (may be truncated if length exceeds limit) |
| `code_truncated` | boolean | `true` if `code` was truncated due to character limit |
| `dependencies` | array | Array of dependency tree nodes (input signals of the current project) |

## Dependency Tree Node (elements of `dependencies` and nested `inputs`)

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | KKS code of the signal |
| `type` | string | Signal type:<br>`"base"` – physical signal from the station (no code, has description)<br>`"synthetic"` – computed by another project (has code and input list)<br>`"cyclic"` – cyclic dependency (not expanded)<br>`"unknown"` – project not found |
| `description` | string | Signal description (from CSV for base, from metadata for synthetic) |
| `dimension` | string | Dimension (only for synthetic, if specified) |
| `code` | string | Code (formula) of the synthetic signal (may be truncated) |
| `code_truncated` | boolean | `true` if `code` was truncated |
| `inputs` | array | Only for `"synthetic"` – array of child nodes (input signals of this project) |

## Notes

- Base signals (`type: "base"`) have no `code`, `code_truncated`, or `inputs` fields.
- Cyclic dependencies (`type: "cyclic"`) contain only `name`, `type`, and `description`.
- Unknown signals (`type: "unknown"`) have `name`, `type`, and `description`.
- Code length is limited by the `max_code_length` setting in the LLM configuration (default 4000 characters). If exceeded, the string is truncated with `"..."` appended, and `code_truncated` is set to `true`.
- Descriptions of base signals are taken from the CSV files loaded in the configuration signals.