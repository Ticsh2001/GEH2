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

## KKS Code Interpretation

KKS codes consist of a letter‑digit combination that identifies the system group, the measurement type, and the specific component. The following tables provide the meaning of the most common prefixes.

### System Groups (first letters)

| Prefix | System Description |
|--------|-------------------|
| `MAA` | High‑pressure part |
| `MAB` | Intermediate‑pressure part |
| `MAC` | Low‑pressure part |
| `MAD` | Bearing system |
| `MAG` | Condenser plant |
| `MAJ` | Air removal system |
| `MAL` | Turbine drain and ventilation system |
| `MAM` | Steam leak detection system |
| `MAN` | Bypass and spray system for superheat reduction |
| `MAV` | Lubrication oil supply system |
| `MAW` | Gland seal system |
| `MAX` | Hydraulic control system |
| `MAY` | Turbine wall temperature measurement system |
| `MYA` | Control system |
| `LAA` | Deaeration in the feedwater system |
| `LAB` | Feedwater piping system |
| `LAC` | Feed pump system |
| `LAD` | High‑pressure heater system |
| `LBA` | Main steam piping system |
| `LBB` | Hot reheat piping system |
| `LBC` | Cold reheat piping system |
| `LBD` | Extraction steam piping system |
| `LBG` | Auxiliary steam piping system (own consumption) |
| `LCA` | Main condensate piping system (excluding condensate pumps, heating and condensate polishing) |
| `LCB` | Main condensate pumps |
| `LCE` | Main condensate injection system |
| `PAB` | Main cooling water duct and pipe system |
| `PAC` | Main cooling water pump station |
| `PAD` | Recirculating cooling water system, blowdown installation |
| `PAN` | Condenser cleaning installation |
| `PAV` | Lubricant supply system (main cooling water system) |

### Measurement/Control Types (following digits and letters)

| Code | Meaning |
|------|---------|
| `AA` | Valves, dampers, etc. |
| `CP` | Pressure |
| `CF` | Flow, velocity |
| `CL` | Level |
| `CM` | Moisture content, humidity |
| `CT` | Temperature |
| `CS` | Speed, velocity, etc. |
| `CY` | Vibration, expansion |
| `CQ` | Quality variables |
| `CG` | Distance, length, position, direction of rotation |
| `FG` | Distance, length, position, direction of rotation (interconnected, corrected, calculated, suppressed scale) |
| `FP` | Pressure (interconnected, corrected, calculated, suppressed scale) |
| `FD` | Density (interconnected, corrected, calculated, suppressed scale) |
| `FF` | Flow, mass flow (interconnected, corrected, calculated, suppressed scale) |
| `FL` | Level (interconnected, corrected, calculated, suppressed scale) |
| `FT` | Temperature (interconnected, corrected, calculated, suppressed scale) |
| `FU` | Combined and other quantities (interconnected, corrected, calculated, suppressed scale) |
| `FS` | Speed, rotational speed, frequency (mechanical), acceleration (interconnected, corrected, calculated, suppressed scale) |
| `DP` | Pressure (control loop) |
| `DD` | Density (control loop) |
| `DF` | Flow, mass flow (control loop) |
| `DL` | Level (control loop) |
| `DT` | Temperature (control loop) |
| `DG` | Distance, length, position, direction of rotation (control loop) |
| `DU` | Combined and other quantities (control loop) |
| `DS` | Speed, rotational speed, frequency (mechanical), acceleration (control loop) |
| `DE` | Electrical quantities (e.g., current, voltage, power, electrical frequency) (control loop) |
| `EU` | Algorithm for obtaining calculated signals from analog and binary quantities, including input/output protection |
| `EG` | Alarm and warning (malfunction) |
| `EP` | Special computer, monitoring computer |
| `EN` | Special computer, status display computer, criteria display |
| `EC` | Step sequence program |
| `EB` | Step sequence program (unit level) |
| `EA` | Step sequence program (plant level) |

## Notes

- Base signals (`type: "base"`) have no `code`, `code_truncated`, or `inputs` fields.
- Cyclic dependencies (`type: "cyclic"`) contain only `name`, `type`, and `description`.
- Unknown signals (`type: "unknown"`) have `name`, `type`, and `description`.
- Code length is limited by the `max_code_length` setting in the LLM configuration (default 4000 characters). If exceeded, the string is truncated with `"..."` appended, and `code_truncated` is set to `true`.
- Descriptions of base signals are taken from the CSV files loaded in the configuration signals.