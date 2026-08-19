# Failure taxonomy

Every failing replicate in the bundle, classified by what the artifact would do to an experiment rather than by which comparator code fired — several codes describe the same underlying mistake. Where a replicate raised more than one code the most severe class is reported, since nothing downstream of a crash was observed.


## Summary

| class | failures | what it means |
|---|---|---|
| `does_not_run` | 42 | raises before finishing — a crash, a syntax error, an exhausted resource |
| `wrong_target` | 7 | runs, and acts on the wrong wells or labware |
| `wrong_quantity` | 6 | runs, and moves or records the wrong amount |
| `unusable_reference` | 4 | emits a name nothing will resolve |
| `blocks_real_time` | 3 | runs, but halts the instrument in wall-clock time |
| `silent_omission` | 1 | reports success and leaves a required output missing |
| **total** | **63** | |

## By series

| series | failures | classes |
|---|---|---|
| FPLC / analysis | 1 | wrong_quantity x1 |
| FPLC / database | 2 | does_not_run x2 |
| FPLC / report | 3 | unusable_reference x3 |
| gpt-5-mini / protocol 1 | 9 | does_not_run x5, wrong_target x4 |
| gpt-5-nano / protocol 1 | 20 | does_not_run x16, wrong_target x3, wrong_quantity x1 |
| gpt-5-nano / protocol 4 | 9 | does_not_run x6, blocks_real_time x3 |
| isotherm / analysis | 4 | does_not_run x2, silent_omission x1, wrong_quantity x1 |
| isotherm / database | 1 | does_not_run x1 |
| isotherm / device protocol 1 | 10 | does_not_run x9, wrong_quantity x1 |
| isotherm / device protocol 2 | 1 | does_not_run x1 |
| isotherm / device protocol 3 | 2 | wrong_quantity x2 |
| isotherm / report | 1 | unusable_reference x1 |

## Every failure

| series | rep | class | what went wrong | codes |
|---|---|---|---|---|
| FPLC / analysis | 12 | `wrong_quantity` | peak height differs | `PEAK_HEIGHT_MISMATCH` |
| FPLC / database | 14 | `does_not_run` | generated code does not parse | `EXEC_FAILED` |
| FPLC / database | 15 | `does_not_run` | generated code does not parse | `EXEC_FAILED` |
| FPLC / report | 08 | `unusable_reference` | invents a placeholder nothing will substitute | `PLACEHOLDER_NOT_AVAILABLE` |
| FPLC / report | 12 | `unusable_reference` | invents a placeholder nothing will substitute | `PLACEHOLDER_NOT_AVAILABLE` |
| FPLC / report | 14 | `unusable_reference` | invents a placeholder nothing will substitute | `PLACEHOLDER_NOT_AVAILABLE` |
| gpt-5-mini / protocol 1 | 01 | `wrong_target` | liquid delivered to a destination the reference does not use | `DEST_EXTRA` |
| gpt-5-mini / protocol 1 | 03 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-mini / protocol 1 | 04 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-mini / protocol 1 | 09 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-mini / protocol 1 | 11 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-mini / protocol 1 | 14 | `wrong_target` | liquid delivered to a destination the reference does not use | `DEST_EXTRA`, `DEST_MISSING` |
| gpt-5-mini / protocol 1 | 15 | `wrong_target` | liquid delivered to a destination the reference does not use | `DEST_EXTRA`, `DEST_MISSING` |
| gpt-5-mini / protocol 1 | 17 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-mini / protocol 1 | 19 | `wrong_target` | liquid delivered to a destination the reference does not use | `DEST_EXTRA`, `DEST_MISSING` |
| gpt-5-nano / protocol 1 | 01 | `does_not_run` | ModuleNotFoundError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 02 | `does_not_run` | MalformedPythonProtocolError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 03 | `does_not_run` | MalformedPythonProtocolError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 04 | `does_not_run` | MalformedPythonProtocolError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 05 | `does_not_run` | MalformedPythonProtocolError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 06 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 07 | `does_not_run` | MalformedPythonProtocolError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 08 | `does_not_run` | MalformedPythonProtocolError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 09 | `wrong_quantity` | destination receives a different volume | `DEST_VOLUME_MISMATCH` |
| gpt-5-nano / protocol 1 | 10 | `wrong_target` | a destination the reference fills is never filled | `DEST_MISSING` |
| gpt-5-nano / protocol 1 | 11 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 12 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 13 | `does_not_run` | MalformedPythonProtocolError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 14 | `does_not_run` | MalformedPythonProtocolError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 15 | `wrong_target` | a destination the reference fills is never filled | `DEST_MISSING` |
| gpt-5-nano / protocol 1 | 16 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 17 | `does_not_run` | MalformedPythonProtocolError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 18 | `wrong_target` | a destination the reference fills is never filled | `DEST_MISSING` |
| gpt-5-nano / protocol 1 | 19 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-nano / protocol 1 | 20 | `does_not_run` | MalformedPythonProtocolError | `SIM_FAILED` |
| gpt-5-nano / protocol 4 | 01 | `blocks_real_time` | calls time.sleep() instead of protocol.delay(), idling the robot for the full incubation | `SIM_TIMEOUT` |
| gpt-5-nano / protocol 4 | 02 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-nano / protocol 4 | 09 | `blocks_real_time` | calls time.sleep() instead of protocol.delay(), idling the robot for the full incubation | `SIM_TIMEOUT` |
| gpt-5-nano / protocol 4 | 10 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-nano / protocol 4 | 12 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-nano / protocol 4 | 14 | `blocks_real_time` | calls time.sleep() instead of protocol.delay(), idling the robot for the full incubation | `SIM_TIMEOUT` |
| gpt-5-nano / protocol 4 | 15 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| gpt-5-nano / protocol 4 | 16 | `does_not_run` | MalformedPythonProtocolError | `SIM_FAILED` |
| gpt-5-nano / protocol 4 | 19 | `does_not_run` | ProtocolEngineExecuteError | `SIM_FAILED` |
| isotherm / analysis | 09 | `does_not_run` | misreads the plate layout | `EXEC_FAILED` |
| isotherm / analysis | 14 | `silent_omission` | reports success, produces no required output | `MISSING_OUTPUT` |
| isotherm / analysis | 16 | `does_not_run` | script exits non-zero | `EXEC_FAILED` |
| isotherm / analysis | 18 | `wrong_quantity` | fitted parameter outside tolerance | `FIT_OUT_OF_TOLERANCE` |
| isotherm / database | 15 | `does_not_run` | writes NaN into a JSON column; Postgres rejects it | `EXEC_FAILED` |
| isotherm / device protocol 1 | 01 | `does_not_run` | runs out of tips mid-protocol | `SIM_FAILED` |
| isotherm / device protocol 1 | 05 | `does_not_run` | runs out of tips mid-protocol | `SIM_FAILED` |
| isotherm / device protocol 1 | 07 | `does_not_run` | runs out of tips mid-protocol | `SIM_FAILED` |
| isotherm / device protocol 1 | 08 | `does_not_run` | runs out of tips mid-protocol | `SIM_FAILED` |
| isotherm / device protocol 1 | 10 | `does_not_run` | runs out of tips mid-protocol | `SIM_FAILED` |
| isotherm / device protocol 1 | 13 | `does_not_run` | runs out of tips mid-protocol | `SIM_FAILED` |
| isotherm / device protocol 1 | 16 | `does_not_run` | exhausts a reagent pool instead of splitting across wells | `SIM_FAILED` |
| isotherm / device protocol 1 | 17 | `wrong_quantity` | delivers 8x the intended volume | `DEST_VOLUME_MISMATCH`, `DEST_VOLUME_MULTIPLE` |
| isotherm / device protocol 1 | 18 | `does_not_run` | aspirates more than the tip holds | `SIM_FAILED` |
| isotherm / device protocol 1 | 20 | `does_not_run` | runs out of tips mid-protocol | `SIM_FAILED` |
| isotherm / device protocol 2 | 15 | `does_not_run` | RuntimeError | `SIM_FAILED` |
| isotherm / device protocol 3 | 05 | `wrong_quantity` | delivers 4x the intended volume | `DEST_VOLUME_MISMATCH`, `DEST_VOLUME_MULTIPLE` |
| isotherm / device protocol 3 | 15 | `wrong_quantity` | delivers 4x the intended volume | `DEST_VOLUME_MISMATCH`, `DEST_VOLUME_MULTIPLE` |
| isotherm / report | 10 | `unusable_reference` | invents a placeholder nothing will substitute | `PLACEHOLDER_NOT_AVAILABLE` |
