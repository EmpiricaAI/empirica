"""The proposal contract that core and Cortex share: the envelope, per-target acknowledgements, the audit trail and the credential guard.

Moved from Cortex into core on David's ruling of 2026-11-06 (contract files only), so a local server and Cortex speak one contract.
Imports nothing at package import: ``core/redaction.py`` reads ``leak_guard`` on the artifact write path.
"""
