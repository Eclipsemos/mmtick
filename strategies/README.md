# Strategy Registry

All strategy definitions live under this directory:

- `live/`: the currently deployed live-trading definition.
- `paper/`: frozen strategies approved only for forward paper observation.
- `components/`: reusable frozen sleeves referenced by complete strategies.
- `archive/`: superseded definitions retained for audit history.

JSON-defined runtimes must reference their machine-readable file in this tree. Parameterized ATR
runtimes remain configured in `config/settings.toml`, with frozen operational rules documented
here. Research reports belong in `reports/`; executable code belongs in `src/mastermind_tick/` or
`scripts/`.
