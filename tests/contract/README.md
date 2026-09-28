# SDS Contract Tests

Run the contract-test gate from the repository root with:

```powershell
make gate-semantics
```

That target uses `scripts/run_semantics_gate.py` to bootstrap
`packages/sds_core/src` before running `tests/contract`. Direct
`pytest tests/contract` without an installed package or equivalent
`PYTHONPATH` is not the supported root entrypoint.
