# Vendored from Cortex

`conformance/` and `../contract/` are copied from the empirica-cortex repository at commit `e134aa21` (2026-11-14: the
`artifacts.import` capability; first vendored at `849e4ef1`). Since 2026-11-25 core keeps only the read tier and
slim group-B rows (method, path, capability, local status): the full group-B contract and the write tier stay in Cortex. Cortex owns
the contract and this suite; core vendors them to test the local server (empirica-local, spec phase 1) against the same
contract Cortex serves. Edit them in the cortex repo and copy them here again, never here alone, or the two halves drift.

Run against a server:

    pytest conformance --url http://127.0.0.1:PORT --token TOKEN            # read tier

Without `--url` (or `CONFORMANCE_URL`) every test is skipped, and core's own `pytest` (testpaths = tests) does not collect
this directory.
