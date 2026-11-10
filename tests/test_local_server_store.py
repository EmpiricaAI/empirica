"""empirica-local's proposal store (spec phase 1.2, goal 92fe2c88): SQLite, one person, proposals land accepted.

Idempotency as Cortex does it since 849e4ef1: a key is scoped to the sender; the same key with the same
parent|title|summary returns the original proposal (a replay), a different body under a used key is refused.
"""

from __future__ import annotations

import pytest

from empirica.core.orchestration.schemas import Proposal
from empirica.local_server import store as st


@pytest.fixture
def db(tmp_path):
    return st.ProposalStore(tmp_path / "local.db")


def _p(title="t", parent=None, targets=("local.me.b",), source="local.me.a"):
    return Proposal(title=title, summary="s", parent_id=parent, source_claude=source, target_claudes=list(targets))


def test_a_proposal_lands_accepted_and_can_be_read_back(db):
    saved, replay = db.propose(_p(), idempotency_key=None)
    assert not replay and saved.status == "accepted"
    assert db.get(saved.id).title == "t"


def test_the_same_key_and_body_twice_is_one_proposal(db):
    first, _ = db.propose(_p(), idempotency_key="k1")
    again, replay = db.propose(_p(), idempotency_key="k1")
    assert replay and again.id == first.id
    assert len(db.outbox("local.me.a")) == 1


def test_a_different_body_under_a_used_key_is_refused(db):
    db.propose(_p(title="one"), idempotency_key="k1")
    with pytest.raises(st.IdempotencyConflict):
        db.propose(_p(title="two"), idempotency_key="k1")


def test_keys_are_scoped_to_the_sender(db):
    a, _ = db.propose(_p(source="local.me.a"), idempotency_key="k1")
    b, replay = db.propose(_p(source="local.me.c"), idempotency_key="k1")
    assert not replay and a.id != b.id


def test_inbox_and_outbox_by_address(db):
    p, _ = db.propose(_p(targets=("local.me.b",)))
    assert [x.id for x in db.inbox("local.me.b")] == [p.id]
    assert db.inbox("local.me.a") == []
    assert [x.id for x in db.outbox("local.me.a")] == [p.id]


def test_archive_is_per_recipient_and_repeatable(db):
    p, _ = db.propose(_p(targets=("local.me.b", "local.me.c")))
    assert db.archive(p.id, "local.me.b") == (True, False)
    assert db.archive(p.id, "local.me.b") == (True, True)  # already archived: the requested state
    assert db.inbox("local.me.b") == [] and [x.id for x in db.inbox("local.me.c")] == [p.id]
    assert db.archive("prop_unknown", "local.me.b") == (False, False)


def test_completing_a_single_target_proposal_records_the_result(db):
    p, _ = db.propose(_p())
    done = db.complete(p.id, "local.me.b", result="shipped", note="did it", commit_sha="abc1234")
    assert done is not None and done.status == "completed" and done.completion_commit_sha == "abc1234"
    assert db.complete("prop_unknown", "local.me.b", result="shipped") is None


def test_a_multi_target_proposal_completes_when_every_target_has_acked(db):
    p, _ = db.propose(_p(targets=("local.me.b", "local.me.c")))
    first = db.complete(p.id, "local.me.b", result="shipped")
    assert first is not None and first.status != "completed"
    second = db.complete(p.id, "local.me.c", result="shipped")
    assert second is not None and second.status == "completed"


def test_an_ack_from_a_practice_that_is_not_a_target_is_refused(db):
    p, _ = db.propose(_p(targets=("local.me.b", "local.me.c")))
    with pytest.raises(st.CompletionRefused) as e:
        db.complete(p.id, "local.me.z", result="shipped")
    assert e.value.status_code == 400


def test_arrival_acceptance_is_recorded_as_the_system_not_a_person(db):
    """No one reviewed it: the local edition accepts on arrival, and the wire must not say a human decided."""
    saved, _ = db.propose(_p())
    assert saved.to_wire()["decided_by_kind"] == "system" and saved.auto_routed is True


def test_two_processes_racing_one_key_get_one_proposal_and_a_replay(tmp_path):
    """Two serve processes on one database file: the second sender with the same key must replay, not fail."""
    import multiprocessing as mp

    path = tmp_path / "shared.db"
    st.ProposalStore(path)
    ctx = mp.get_context("fork")
    start, out = ctx.Barrier(6), ctx.Queue()

    def one():
        start.wait()
        try:
            saved, replay = st.ProposalStore(path).propose(_p(), idempotency_key="same")
            out.put((saved.id, replay))
        except Exception as e:  # the failure is the thing under test
            out.put(("error", repr(e)))

    procs = [ctx.Process(target=one) for _ in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(20)
    results = [out.get(timeout=5) for _ in range(6)]
    assert all(r[0] != "error" for r in results), results
    assert len({r[0] for r in results}) == 1 and sum(1 for r in results if not r[1]) == 1


def test_no_connection_is_left_open_after_a_store_call(tmp_path):
    """`with sqlite3.connect(...)` commits but does not close: every call left the database, its WAL and its shared-memory
    file open until garbage collection, and a forked race test then committed two proposals for one key (CI, 3.13)."""
    import os

    def handles():
        out = []
        for fd in os.listdir("/proc/self/fd"):
            try:
                out.append(os.readlink(f"/proc/self/fd/{fd}"))
            except OSError:
                pass
        return [h for h in out if str(tmp_path) in h]

    store = st.ProposalStore(tmp_path / "s.db")
    assert handles() == []
    saved, _ = store.propose(_p(), idempotency_key="k")
    store.get(saved.id)
    store.inbox(saved.distinct_targets()[0])
    assert handles() == []


def test_the_registry_leaves_no_connection_open(tmp_path):
    import os

    from empirica.local_server.api import ProjectRegistry

    reg = ProjectRegistry(tmp_path / "r.db")
    reg.register("alpha", None, None, None)
    reg.unregister(None, "alpha")
    left = []
    for fd in os.listdir("/proc/self/fd"):
        try:
            left.append(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            pass
    assert [h for h in left if str(tmp_path) in h] == []
