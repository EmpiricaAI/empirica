"""Read-only platform inspection flows ungated; the mutating mode of the same tool does not.

Reported by empirica.philipp.empirica-mesh-support (prop_iivc7beoyjh4rh4fhohff74uim), observed on a macOS box in two seats
between 2026-10-11 and 2026-10-16: inside an open transaction without claims, `plutil -p`, `defaults read`, `sw_vers`,
`pmset -g log | grep`, `docker inspect` and `$(launchctl list | awk ...)` were refused as praxic.

Two causes, both measured before the fix:

1. The macOS and docker read verbs lived only in INFRA_SAFE_PREFIXES, which applies only under work_type
   infra/config/debug/remote-ops, and only to a SINGLE command with no pipe. A read is a read in any work_type, and the
   same verb inside a pipe, a chain segment or a `$(...)` body was refused even under infra.
2. `defaults read` was on no list at all.

Every read below is paired with the write mode of the same tool. The reads being allowed must never make a write reachable,
which is why the guards are flag-aware rather than name-aware.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture(scope="module")
def gate_module():
    sys.path.insert(0, str(_HOOK_DIR.parent / "lib"))
    spec = importlib.util.spec_from_file_location("sentinel_gate_platform_reads", _HOOK_DIR / "sentinel-gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=[None, "code", "research"])
def safe(gate_module, monkeypatch, request):
    """`safe(cmd)` is True when the gate lets `cmd` through ungated, in a work_type that has no infra expansion."""
    monkeypatch.setattr(gate_module, "_current_work_type", request.param)
    return lambda cmd: gate_module.is_safe_bash_command({"command": cmd}) is True


READS = [
    # the commands Philipp's seats were refused on
    "plutil -p /Applications/Claude.app/Contents/Info.plist",
    "plutil -p /Applications/Claude.app/Contents/Info.plist | grep -E 'Version|Build'; sw_vers -productVersion",
    "defaults read /Applications/Claude.app/Contents/Info.plist CFBundleShortVersionString; sw_vers -productVersion",
    "pmset -g log | grep -E ' (Sleep|Wake) ' | tail -3",
    "docker inspect c1 --format '{{range .Mounts}}{{.Source}}{{end}}'",
    "echo \"$(launchctl list | awk '/x$/{print $1}')\"",
    # variations of the same families
    "plutil -p /x.plist | head -3",
    "plutil -lint /x.plist",
    "defaults read com.apple.dock",
    "defaults read -g AppleInterfaceStyle",
    "sw_vers",
    "sw_vers -productVersion",
    "pmset -g",
    "pmset -g batt",
    "launchctl list",
    "launchctl list com.example.job",
    "launchctl print gui/501/com.example.job",
    "docker ps",
    "docker ps -a --format '{{.Names}}'",
    "docker ps -q | wc -l",
    "docker images",
    "docker inspect c1 | jq '.[0].Mounts'",
    "docker logs --tail 20 c1",
    "docker network ls",
    "docker volume ls",
    "vm_stat",
    "diskutil list",
    "diskutil info /",
    "scutil --get ComputerName",
    "system_profiler SPSoftwareDataType",
    'n=$(docker ps -q | wc -l); [ "$n" -gt 0 ] && echo up',
    'echo "$([ "$n" -gt 2 ] && echo y)"',
    "X=$(launchctl list | awk '/x$/{print $1}'); echo $X",
]


WRITES = [
    # plutil: every edit mode, and an output file on the read mode
    "plutil -replace a -string b /x.plist",
    "plutil -insert a -string b /x.plist",
    "plutil -remove a /x.plist",
    "plutil -convert xml1 /x.plist",
    "plutil -extract a raw -o /tmp/out /x.plist",
    "plutil -create xml1 /x.plist",
    "plutil -p /x.plist -o /tmp/out",
    "plutil -p /x.plist > /tmp/out",
    # defaults
    "defaults write com.apple.dock x -int 1",
    "defaults delete com.apple.dock x",
    "defaults import com.apple.dock /x.plist",
    "defaults rename com.apple.dock a b",
    "defaults read com.apple.dock; defaults write com.apple.dock x -int 1",
    "defaults read com.apple.dock > /tmp/out",
    # pmset
    "pmset sleepnow",
    "pmset displaysleepnow",
    "pmset -a sleep 0",
    "pmset -b displaysleep 5",
    "pmset schedule wake '10/20/26 08:00:00'",
    "pmset -g; pmset sleepnow",
    "pmset -g sleepnow",
    # docker
    "docker exec c1 rm -rf /x",
    "docker run --rm alpine true",
    "docker rm c1",
    "docker rmi img",
    "docker stop c1",
    "docker kill c1",
    "docker cp c1:/x /tmp/x",
    "docker commit c1 img",
    "docker build .",
    "docker pull img",
    "docker network create n",
    "docker volume rm v",
    "docker ps; docker rm c1",
    "docker inspect c1 | sh",
    "docker inspect c1 > /tmp/out",
    # launchctl
    "launchctl load /x.plist",
    "launchctl unload /x.plist",
    "launchctl kickstart -k gui/501/com.x",
    "launchctl bootstrap gui/501 /x.plist",
    "launchctl bootout gui/501/com.x",
    "launchctl remove com.x",
    "launchctl start com.x",
    "launchctl stop com.x",
    "launchctl enable gui/501/com.x",
    "launchctl disable gui/501/com.x",
    "launchctl setenv A b",
    "launchctl list; launchctl bootout gui/501/com.x",
    # diskutil / scutil / system_profiler
    "diskutil eraseDisk APFS x disk2",
    "diskutil unmount /Volumes/x",
    "diskutil apfs deleteContainer disk2",
    "scutil --set ComputerName x",
    "system_profiler SPSoftwareDataType > /tmp/out",
    # a read-only look-alike prefix is not the read
    "docker psx",
    "docker inspectx c1",
    "launchctl listx",
    "sw_versx",
    # substitutions whose body is a write
    "echo $(defaults write a b c)",
    'echo "$(launchctl kickstart gui/501/com.x)"',
    "X=$(docker rm c1); echo $X",
    "echo $(plutil -replace a -string b /x.plist)",
    "echo $(pmset sleepnow)",
]


@pytest.mark.parametrize("command", READS)
def test_platform_read_flows_ungated(safe, command):
    assert safe(command), command


@pytest.mark.parametrize("command", WRITES)
def test_the_write_mode_of_the_same_tool_stays_gated(safe, command):
    assert not safe(command), command


def test_the_harness_is_live(safe):
    """Positive control: the probe says yes to a plain read and no to a plain write, so the two lists above mean something."""
    assert safe("ls -la")
    assert not safe("rm -rf /tmp/x")


def test_a_bare_unquoted_substitution_is_judged_like_any_other_command(safe):
    """`echo $(date)` is gated in every work_type by a documented rule about unquoted substitution, not about the inner command.
    A platform read inside it must be judged the same way, so the inner-command refusal Philipp saw ("not a recognised read")
    is gone while the unquoted-form rule is untouched."""
    assert safe("echo $(launchctl list | awk '/x$/{print $1}')") == safe("echo $(date)")


# The flag guards read the command with shlex, which does not expand what bash expands. A write flag spelled so that only
# the shell sees it must not get past a guard that looks for the literal word.
SHELL_SPELLED_WRITES = [
    "plutil -p /x.plist $'-convert' xml1",
    "plutil -p /x.plist {-convert,xml1}",
    "plutil -p /x.plist \\-convert xml1",
    "defaults read x | plutil -p - $'-o' /tmp/o",
    "env FOO=1 plutil -p /x $'-convert'",
    "pmset -g $'sleepnow'",
    "pmset -g {sleepnow,}",
    "pmset -g sleep\\now",
    "env FOO=1 pmset -g $'sleepnow'",
]

# `pmset -g <word> <value>` is not a documented query. The guard is an allowlist of query words, so a setting name, a case
# variant or a stray selector gates instead of being guessed safe.
PMSET_NOT_A_QUERY = [
    "pmset -g sleep 0",
    "pmset -g displaysleep 1",
    "pmset -g hibernatemode 0",
    "pmset -g womp 1",
    "pmset -g powernap 0",
    "pmset -g SLEEPNOW",
    "pmset -g SleepNow",
    "pmset -g -A sleep 0",
    "pmset -g --all sleep 0",
    "pmset -g log sleep 0",
]

PMSET_QUERIES = [
    "pmset -g",
    "pmset -g log",
    "pmset -g batt",
    "pmset -g ps",
    "pmset -g assertions",
    "pmset -g custom",
    "pmset -g live",
    "pmset -g thermlog",
    "pmset -g adapter",
    "pmset -g sched",
]


@pytest.mark.parametrize("command", SHELL_SPELLED_WRITES)
def test_a_write_flag_only_the_shell_can_see_stays_gated(safe, command):
    assert not safe(command), command


@pytest.mark.parametrize("command", PMSET_NOT_A_QUERY)
def test_pmset_g_with_anything_but_a_query_word_stays_gated(safe, command):
    assert not safe(command), command


@pytest.mark.parametrize("command", PMSET_QUERIES)
def test_pmset_g_query_words_flow(safe, command):
    assert safe(command), command
