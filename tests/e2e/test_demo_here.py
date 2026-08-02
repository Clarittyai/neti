"""`neti demo --here` against a fixture with known contents.

The demo's whole claim is that its numbers are real, so the test has to be able to say what the
right number *is*. A tree built here with an exact file count does that; pointing it at the
repository would make the assertions drift with every commit.

Two things carry most of the weight:

- **the first run**, with no traffic at all, because that is what every evaluator gets. It has to
  produce the reach finding and say plainly what is missing, rather than printing an empty report.
- **what the demo claims**, because an overstated headline discredits every honest number beside
  it. The first draft said "an agent can touch 35,871 objects in a single Edit call", which is
  flatly false — `Edit` takes one `file_path` and touches one file. Reachable-max is a property of
  the resolver and its root, and the tool that happened to sort first was being credited with the
  whole tree.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from neti.config.policy import load_policy
from neti.eval.corpus import Corpus, capture, load_corpus, write_corpus
from neti.eval.here import HERE_DISCLAIMER, run_here
from neti.preflight import Preflight
from neti.store.jsonl import read_records

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "coding-agent.yaml"

FILES = 340
"""Files in the fixture. Every assertion below is against this number rather than against whatever
the repository happens to contain today."""

VENDOR = 300
"""A big vendored directory, because that is the shape real repositories have and the shape the
product exists for: `node_modules`, `vendor`, `.venv` — one short argument addressing most of the
tree while every ordinary call touches a handful of files."""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for name, count in (("src", 30), ("tests", 10), ("vendor", VENDOR)):
        (root / name).mkdir(parents=True)
        for i in range(count):
            (root / name / f"f{i}.py").write_text("x" * 20)
    return root


def traffic(repo: Path, tmp_path: Path, n: int = 40) -> Corpus:
    """A corpus captured the way a real one is: by gating real calls and reading the log back.

    Not hand-written. `capture` is the thing under test as much as the demo is, and a corpus
    assembled by hand would not exercise the path that produces one.
    """
    records = tmp_path / "captured.ndjson"
    pf = Preflight.demo(EXAMPLE, mode="observe", records=records)
    # Bimodal on purpose: mostly ordinary work, occasionally the whole vendored tree. Uniform
    # traffic correctly gets a ceiling above its own maximum — `propose` has a property test
    # asserting exactly that — so a flat corpus would prove nothing about enforcement.
    for i in range(n):
        target = repo / ("vendor" if i % 10 == 0 else "tests")
        pf.check("Grep", {"path": str(target)})
    return capture(read_records(records), repo, source="a fixture session")


# ---------------------------------------------------------------------------- the first run


def test_with_no_traffic_it_still_measures_the_machine(repo: Path) -> None:
    """The run every evaluator gets first. Acts 1 and 2 need nothing but the directory."""
    result = run_here(repo, EXAMPLE)

    assert result.reach, "the reach table is the whole day-one finding"
    assert all(row.reachable.magnitude == FILES for row in result.reach)
    assert result.findings, "no traffic must still produce a finding"
    assert not result.has_traffic
    assert result.next_steps, "and it must say how to get the rest"
    assert any("hook" in line for line in result.next_steps)


def test_the_reach_number_is_the_real_file_count(repo: Path) -> None:
    """Measured, not modelled. If this drifts, every other number in the demo is suspect."""
    result = run_here(repo, EXAMPLE)
    counted = sum(1 for p in repo.rglob("*") if not p.is_dir())

    assert counted == FILES
    assert result.reach[0].reachable.magnitude == counted


def test_the_headline_does_not_credit_one_tool_with_the_whole_tree(repo: Path) -> None:
    """The overstatement this file exists to prevent.

    `reachable_max` belongs to the resolver and the root. Attaching it to a tool — "in a single
    Edit call" — is false for any tool whose parameter names a single file, and false in the
    direction that flatters the product.
    """
    headline = run_here(repo, EXAMPLE).findings[0].headline

    assert f"{FILES:,}" in headline
    assert "in a single" not in headline, "reach is not a per-call measurement"
    for tool in ("Edit", "Read", "Write"):
        assert f"single {tool}" not in headline


def test_the_finding_says_what_kind_of_number_it_is(repo: Path) -> None:
    """A bound on capability, said in the sentence rather than in a footnote nobody reads."""
    detail = run_here(repo, EXAMPLE).findings[0].detail
    assert "does not measure any single call" in detail
    assert "bounds what one credential can address" in detail


# ---------------------------------------------------------------------------- with traffic


def test_the_whole_lifecycle_runs_on_real_files(repo: Path, tmp_path: Path) -> None:
    """All six acts, and the audit at the end has to close over what the earlier acts wrote."""
    result = run_here(repo, EXAMPLE, corpus=traffic(repo, tmp_path))

    assert result.has_traffic
    assert result.observed["allow"] == result.corpus_size, "observe mode never stops anything"
    assert result.report is not None and result.report.decisions == result.corpus_size
    assert result.records == result.corpus_size
    assert result.chain_ok, "the chain the demo itself wrote must verify"
    assert result.replayed == result.records, "and every decision must re-derive"


def test_the_proposed_ceilings_are_actually_applied(repo: Path, tmp_path: Path) -> None:
    """Act 5 has to enforce the numbers act 4 proposed.

    Otherwise the demo is showing two unrelated runs side by side and calling it a consequence.
    """
    result = run_here(repo, EXAMPLE, corpus=traffic(repo, tmp_path))

    assert result.proposals, "40 calls is above MIN_SAMPLES, so something must be proposable"
    assert sum(result.enforced.values()) == result.corpus_size
    interrupts = result.enforced.get("block", 0) + result.enforced.get("confirm", 0)
    assert interrupts > 0, (
        "ceilings derived from this traffic caught none of it — the same failure `propose` has a "
        "property test for"
    )


def test_targets_that_do_not_exist_here_are_counted_not_hidden(repo: Path, tmp_path: Path) -> None:
    """A corpus captured elsewhere will not fit perfectly, and averaging that away would quietly
    turn a poor fit into a confident report."""
    from neti.eval.corpus import Call

    borrowed = Corpus(calls=(Call(tool="Grep", pointer="/path", target="not/here"),))
    result = run_here(repo, EXAMPLE, corpus=borrowed)

    assert result.unresolved == 1


# ---------------------------------------------------------------------------- the corpus itself


def test_capture_keeps_paths_relative_and_drops_everything_outside(
    repo: Path, tmp_path: Path
) -> None:
    """A corpus is a thing people share. Absolute paths carry home directories, client names and
    machine names, so anything outside the root is dropped rather than scrubbed — a half-scrubbed
    path is worse than a missing one because it looks safe."""
    records = tmp_path / "mixed.ndjson"
    pf = Preflight.demo(EXAMPLE, mode="observe", records=records)
    pf.check("Grep", {"path": str(repo / "src")})
    pf.check("Grep", {"path": str(tmp_path)})  # outside the repo root

    corpus = capture(read_records(records), repo)

    assert [c.target for c in corpus.calls] == ["src"]
    assert not any(str(repo) in c.target for c in corpus.calls)


def test_a_corpus_round_trips_through_disk(repo: Path, tmp_path: Path) -> None:
    original = traffic(repo, tmp_path, n=5)
    path = tmp_path / "corpus.jsonl"
    write_corpus(original, path)

    assert load_corpus(path).calls == original.calls
    assert load_corpus(path).source == original.source


def test_the_corpus_file_is_readable_without_a_parser(repo: Path, tmp_path: Path) -> None:
    """Somebody is being asked to run this against their own repository. They should be able to
    read it in a terminal first, which rules out anything more compact than one object per line."""
    path = tmp_path / "corpus.jsonl"
    write_corpus(traffic(repo, tmp_path, n=3), path)
    lines = path.read_text().splitlines()

    assert len(lines) == 4, "a header plus one line per call"
    assert all(line.startswith("{") and line.endswith("}") for line in lines)


# ---------------------------------------------------------------------------- through the CLI


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "neti.cli", *args], capture_output=True, text=True, cwd=cwd
    )


def test_the_command_runs_and_names_its_disclaimer(repo: Path) -> None:
    out = run("demo", "--here", "--repo", str(repo), "-c", str(EXAMPLE), cwd=repo)

    assert out.returncode == 0, out.stderr
    assert "REACH" in out.stdout
    assert f"{FILES:,}" in out.stdout
    assert "Measured on this machine" in out.stdout


def test_with_traffic_the_command_prints_all_six_acts(repo: Path, tmp_path: Path) -> None:
    path = tmp_path / "corpus.jsonl"
    write_corpus(traffic(repo, tmp_path), path)

    out = run(
        "demo", "--here", "--repo", str(repo), "-c", str(EXAMPLE), "--corpus", str(path), cwd=repo
    )

    assert out.returncode == 0, out.stderr
    for act in ("DISCOVER", "REACH", "OBSERVE", "REPORT & PROPOSE", "ENFORCE", "AUDIT"):
        assert act in out.stdout, f"act {act} missing from the demo"
    assert "borrowed" in out.stdout, "the act-3 caveat must survive into the output"


def test_the_disclaimer_separates_what_is_measured_from_what_is_borrowed() -> None:
    """The sentence that has to survive a hostile read, pinned so it cannot soften.

    Its synthetic sibling is allowed to say "not a finding". This one claims a finding, so it has
    to be exact about which half is measured and which half came from somebody else's session.
    """
    assert "Measured on this machine" in HERE_DISCLAIMER
    assert "magnitudes are yours" in HERE_DISCLAIMER
    assert "borrowed" in HERE_DISCLAIMER


def test_a_missing_corpus_file_is_an_error_not_a_silent_empty_run(repo: Path) -> None:
    out = run("demo", "--here", "--repo", str(repo), "--corpus", "/nope/absent.jsonl", cwd=repo)

    assert out.returncode == 2
    assert "cannot read corpus" in out.stderr


def test_a_capped_walk_is_reported_as_a_floor_not_a_total(repo: Path) -> None:
    """Found by running the demo on a 712,359-file tree, where it said "reaches 200,000 objects".

    200,000 is the cap, not the answer — the real figure was three and a half times larger. A
    capped count is a `LOWER_BOUND` and the sentence has to carry that, both because it is what the
    resolver reported and because the floor is the more alarming number anyway: "at least 200,000,
    and we stopped counting" is the honest version *and* the stronger one.
    """
    policy = load_policy(EXAMPLE).model_copy(
        update={"providers": {"fs": {"root": str(repo), "cap": 5}}}
    )
    result = run_here(repo, EXAMPLE, policy_override=policy)

    assert result.reach[0].reachable.magnitude == 5
    assert "at least 5" in result.findings[0].headline
    assert "floor rather than a total" in result.findings[0].detail


def test_an_uncapped_walk_says_no_such_thing(repo: Path) -> None:
    """The other direction: an exact count must not be hedged into uselessness."""
    finding = run_here(repo, EXAMPLE).findings[0]

    assert "at least" not in finding.headline
    assert "floor" not in finding.detail
