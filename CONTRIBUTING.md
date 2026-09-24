# Contributing

## Setup

```bash
git clone https://github.com/GianlucaRoma/builddiet
cd builddiet
pip install -e .
```

Python 3.9+ with no dependencies. `git` is optional.

## Tests

```bash
python -m unittest discover -s tests -t .
```

The tests put every temporary folder and sandbox in one test area. That area is `$BUILDDIET_TEST_TMP`, or `builddiet-tests` on the local drive with the most free space. The suite refuses to start with less than 1 GB free there. It never reads or writes your real `~/.builddiet`. See [docs/TEST-ISOLATION.md](docs/TEST-ISOLATION.md).

## Release gates

A change to what BuildDiet proves or deletes must keep the gates green:

* [BD-ZERO](docs/BD-ZERO.md): `benchmarks/bd_zero/`.
* [BD-REAL](docs/BD-REAL.md): `benchmarks/bd_real/`.
* [BD-WATCH](docs/BD-WATCH.md).
* [macOS validation](docs/MAC-VALIDATION.md): both functional gates, multiple Python versions, package installation and restore.

Each gate document says how to reproduce it. Run the gates on scratch folders, never on your own projects.

## Rules

* Only `reclaim.py` deletes outside sandboxes. Any new deletion path needs an invariant in [docs/SAFETY.md](docs/SAFETY.md) and a regression test.
* A change that can delete more must also make BuildDiet prove more. Never weaken a check to get more items proven.
