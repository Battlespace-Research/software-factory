# rangering

A tiny Python package that labels the range rings drawn around a point on a map: `format_range(meters, unit)` returns a label such as `500 m`, `2.5 km` or `1.3 nmi`.

It is the sample project for the software factory's acceptance run ([`docs/acceptance/v1-checklist.md`](../../docs/acceptance/v1-checklist.md)). It has one deliberate bug, found and fixed during that run: leave it in place here, and work on a copy.

## Build and test

Python 3.11 or newer, standard library only.

| Command | What it runs |
|---|---|
| `python verify.py --quick` | the unit tests |
| `python verify.py` | the unit tests, then a byte-compile of every module (`python -m compileall`) |

Both exit 0 when everything passes.
