"""Seam C: the repository boundary.

The engine repository must never carry personal state. Applications, Documents and correspondence
live in `jobs-data`, cloned into the ignored `data/` directory. These tests read the git index, not
the working tree, so they fail on what is about to be committed rather than on local clutter.

Saved pages under `tests/fixtures/` are the one place a contact address legitimately appears:
reading one off a posting is a feature, so extraction is tested against markup shaped the way a real
site serves it. The people in those pages are invented, and their addresses are at `example.org`,
which is what lets the corpus be checked rather than trusted.
"""

import ipaddress
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

CORPUS = ("tests/fixtures/", "tests/test_extraction.py")

DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".doc", ".odt", ".rtf", ".pages"})

# RFC 2606 keeps these for documentation, so an address at one of them belongs to nobody.
INVENTED_DOMAINS = ("example.com", "example.net", "example.org", "example.edu")


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return [path for path in result.stdout.split("\0") if path]


def tracked_texts() -> list[tuple[str, str]]:
    """Every tracked file that reads as text, with what it holds."""
    texts = []
    for path in tracked_files():
        try:
            texts.append((path, (REPO_ROOT / path).read_text(encoding="utf-8")))
        except OSError, UnicodeDecodeError:
            continue
    return texts


def test_the_data_directory_is_never_tracked() -> None:
    offenders = [p for p in tracked_files() if p == "data" or p.startswith("data/")]
    assert offenders == [], "personal state escaped into the engine repo: " + ", ".join(offenders)


def test_contact_addresses_stay_in_the_corpus() -> None:
    offenders = []
    for path, text in tracked_texts():
        if path.startswith(CORPUS):
            continue
        if EMAIL.search(text):
            offenders.append(path)
    assert offenders == [], "email addresses belong in the corpus, not in: " + ", ".join(offenders)


def test_the_corpus_invents_the_people_in_its_saved_pages() -> None:
    """A saved page carries whoever the site named. Only invented ones may be committed."""
    offenders = []
    for path, text in tracked_texts():
        if not path.startswith(CORPUS):
            continue
        offenders += [
            f"{path}: {address}"
            for address in dict.fromkeys(EMAIL.findall(text))
            if not address.lower().endswith(INVENTED_DOMAINS)
        ]
    assert offenders == [], "real people are in the corpus: " + ", ".join(offenders)


def test_application_documents_are_never_tracked() -> None:
    offenders = [
        path
        for path in tracked_files()
        if Path(path).suffix.lower() in DOCUMENT_SUFFIXES and not path.startswith("tests/fixtures/")
    ]
    assert offenders == [], "Documents live in jobs-data, not here: " + ", ".join(offenders)


def test_the_tree_carries_a_licence() -> None:
    assert "LICENSE" in tracked_files(), "the licence is not in the index"


def test_the_tickets_stay_out_of_the_tree() -> None:
    """Specs and tickets are issues now; the tree holds the engine alone."""
    offenders = [p for p in tracked_files() if p.startswith(".scratch/")]
    assert offenders == [], "tickets belong in the issue tracker: " + ", ".join(offenders)


# What names the owner's own deployment: its addresses, its tailnet and its hostnames. The values
# are never written here, which would put them back; these describe them instead. Saved pages are
# exempt, as they are for contact addresses: they are other people's sites.
NOT_A_DEPLOYMENT = ("tests/fixtures/pages/",)

DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")
)

IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(/\d{1,2})?(?![\w.]*\d)")
IPV6 = re.compile(
    r"(?<![\w:])([0-9a-f]{1,4}:(?:[0-9a-f]{0,4}:){1,6}[0-9a-f]{0,4})(/\d{1,3})?(?![\w:])", re.I
)

# A tailnet is named by the label before ts.net, e.g. `<machine>.<tailnet>.ts.net`.
TAILNET = re.compile(r"(?<![\w-])([a-z0-9-]+)\.ts\.net\b", re.I)

# A hostname is judged only where it names the deployment: as the service's own, which by convention
# starts with its name, or as the value of a host setting. Every hostname anywhere would take in the
# public sites the code and docs rightly name, so another subdomain in passing prose goes unseen.
SERVICE_HOSTNAME = re.compile(r"(?<![\w.-])(jobs(?:\.[a-z0-9-]+)+\.[a-z]{2,})\b", re.I)

# A setting that names a host, given a value as `NAME=value` or in a table row `| NAME | value |`.
HOST_SETTING = re.compile(
    r"\b((?:[A-Z][A-Z0-9_]*_)?(?:HOSTNAME|HOST|URL))`?(?:=|\s*\|\s*`)([^\s`|]+)"
)

# RFC 2606 and RFC 6761 keep these, and a tailnet called `example` stands for anyone's.
EXAMPLE_TLDS = frozenset({"example", "test", "invalid", "localhost"})
EXAMPLE_DOMAINS = INVENTED_DOMAINS + ("example.ts.net",)


def is_example_host(hostname: str) -> bool:
    hostname = hostname.lower().rstrip(".")
    return hostname.rsplit(".", 1)[-1] in EXAMPLE_TLDS or any(
        hostname == domain or hostname.endswith("." + domain) for domain in EXAMPLE_DOMAINS
    )


def is_somebodys_address(address: str, prefix: str | None) -> bool:
    """An address that belongs to a machine, as opposed to a documentation one or a range."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if prefix:
        try:
            network = ipaddress.ip_network(address + prefix)  # strict: no host bits set
        except ValueError:
            return True
        if network.num_addresses > 1:
            return False
    if ip.is_loopback or ip.is_unspecified:
        return False
    return not any(ip in network for network in DOCUMENTATION_NETWORKS)


def hostname_of(value: str) -> str | None:
    host = urlsplit(value).hostname if "://" in value else value.split(":", 1)[0]
    if not host or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host, re.I):
        return None
    try:
        ipaddress.ip_address(host)  # an address, which the address patterns judge
    except ValueError:
        return host
    return None


def deployment_specifics(text: str) -> list[str]:
    """What in `text` names one deployment rather than standing in for any."""
    found = [
        match[0]
        for pattern in (IPV4, IPV6)
        for match in pattern.findall(text)
        if is_somebodys_address(match[0], match[1])
    ]
    found += [
        tailnet
        for tailnet in (f"{label}.ts.net" for label in TAILNET.findall(text))
        if not is_example_host(tailnet)
    ]
    found += [host for host in SERVICE_HOSTNAME.findall(text) if not is_example_host(host)]
    for _, value in HOST_SETTING.findall(text):
        host = hostname_of(value)
        if host and not is_example_host(host):
            found.append(host)
    return list(dict.fromkeys(found))


def test_the_tree_names_no_deployment_of_its_own() -> None:
    offenders = []
    for path, text in tracked_texts():
        if path.startswith(NOT_A_DEPLOYMENT):
            continue
        offenders += [f"{path}: {value}" for value in deployment_specifics(text)]
    assert offenders == [], "only example hosts and documentation addresses: " + ", ".join(
        offenders
    )


# The offenders below are invented, and assembled at run time so that this file holds none.
def dotted(*parts: object) -> str:
    return ".".join(str(part) for part in parts)


@pytest.mark.parametrize(
    "text",
    [
        f"SERVER_PUBLIC_IP={dotted(198, 18, 4, 9)}",
        f"its tailnet address `{dotted(100, 77, 3, 12)}`",
        f"`address: {dotted(100, 77, 3, 12)}:443`",
        f"a bridge address, {dotted(172, 18, 0, 5)}.",
        f"one host as a range: {dotted(10, 1, 2, 3)}/32",
        "SERVER_PUBLIC_IPV6=" + ":".join(["2a01", "4f8", "c0c", "1234", "", "1"]),
        "DOKPLOY_URL=http://box.tail1a2b3c" + ".ts.net:3000",
        "the machine `box.tail1a2b3c" + ".ts.net`",
        "as `https://jobs." + "someone.dev`",
        "Domains: `jobs." + "someone.dev`, container port 8000",
        "JOBS_HOSTNAME=jobs." + "someone.dev",
        "HOST=box." + "someone.dev",
        "| `JOBS_HOSTNAME` | `apply." + "someone.dev` |",
        "| `DOKPLOY_URL` | `http://deploy." + "someone.dev:3000` |",
    ],
)
def test_one_deployment_is_recognised(text: str) -> None:
    assert deployment_specifics(text) != []


@pytest.mark.parametrize(
    "text",
    [
        "SERVER_PUBLIC_IP=203.0.113.7",
        "the documentation ranges, 192.0.2.1 and 198.51.100.20",
        "listens on 127.0.0.1:8000, or 0.0.0.0 inside the container",
        "HOST=0.0.0.0 PORT=8000",
        "sourceRange: [100.64.0.0/10, fd7a:115c:a1e0::/48]",
        "SERVER_PUBLIC_IPV6=2001:db8::7",
        "its tailnet address, `100.x.y.z`",
        "DOKPLOY_URL=http://dokploy.example.ts.net:3000",
        "only issues certificates for `<machine>.<tailnet>.ts.net`",
        "JOBS_HOSTNAME=jobs.example.com",
        "| `JOBS_HOSTNAME` | `jobs.example.com` |",
        "| `DATA_REPO_URL` | Dokploy environment | HTTPS URL of the repository |",
        "DOKPLOY_URL: ${{ secrets.DOKPLOY_URL }}",
        "PLAYWRIGHT_VERSION=1.55.0 and Python 3.14",
        "from jobs.web import create_app",
        "a::before, x[::2], 12:30:45",
    ],
)
def test_stand_ins_are_not_mistaken_for_one_deployment(text: str) -> None:
    assert deployment_specifics(text) == []
