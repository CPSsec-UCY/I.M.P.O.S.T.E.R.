# Contributing

Contributions to the Industrial Simulator Ecosystem are welcome. This project is
released under the **EUPL-1.2** licence; by contributing you agree that your
contributions are distributed under the same terms.

## How to contribute

1. Fork the repository and create a feature branch (`git checkout -b feature/...`).
2. Keep changes focused and accompanied by a clear commit message.
3. Ensure the codebase passes the checks below before opening a pull request.
4. Open a pull request against the `main` branch and describe the motivation and
   the testing you performed.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py          # starts the HMI on http://localhost:5000
```

## Coding guidelines

- Python code targets Python 3.10+ and follows PEP 8 (`flake8` / `black` friendly).
- Front-end is dependency-free vanilla JS + CSS; avoid introducing build steps.
- Each plant simulator lives in `simulation/plants/` and must expose a
  `snapshot()`, `step()`, and `control_equipment()` interface.
- New protocol surfaces must be registered in `simulation/protocols.py` with a
  unique, non-conflicting port set.

## Tests

```bash
python3 -m py_compile app.py simulation/*.py simulation/plants/*.py
node --check static/js/hmi.js && node --check static/js/main.js
python3 -c "from simulation.manager import SimulationManager; \
SimulationManager().step_all(False, None, None); print('step OK')"
```

## Reporting issues

Open an issue describing the plant, the observed behaviour, and the expected
behaviour. Include simulator logs or the relevant `/api/state` payload where
possible.

## Licence note

Derivative Works must keep the EUPL notice and be distributed under the EUPL
(or a compatible licence listed in the EUPL appendix).
