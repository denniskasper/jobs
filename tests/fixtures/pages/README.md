# Saved pages

The corpus Seam B runs against. Each file is the bytes a real site served to a plain HTTP GET on
2026-09-18, except the four deliberately malformed ones and the invented ones. `.gitattributes`
keeps git from normalising line endings, so the bytes stay the ones that were served.

The people in the saved pages do not exist either. A staff page or a posting names whoever the site
named, and a test needs the markup, never the person, so every name, contact address, profile link
and direct-dial number was replaced with an invented one before the page was committed. The markup
is otherwise untouched: the TYPO3 mailto tokens are re-encoded with the same rotation vector they
came with, so they still hide an address the way the real ones did. Addresses sit at `example.org`,
which is what `tests/test_no_personal_data.py` checks — a page with a real address in it cannot be
committed.

The institutions are real and stay real: an institute is not a person, and the table below is the
record of where each page came from.

When a site changes, save the new page beside the old one rather than over it: the old page is the
proof of what extraction once had to read. Anonymise it first.

| File | Served from | What it is |
| --- | --- | --- |
| `tuhh-fds-job-vacancies.html` | `https://www.tuhh.de/fds/research/job-vacancies` | An institute page that only signposts the central portal, and invites unsolicited applications |
| `tuhh-listing-research-staff.html` | `https://www.tuhh.de/tuhh/tu-hamburg/arbeiten-an-der-tu-hamburg/stellenausschreibungen/wissenschaftliche-mitarbeit` | A listing whose posting links are in static markup, in `data-url` on table rows rather than in anchors |
| `tuhh-posting-digital-economics.html` | `https://stellenportal.tuhh.de/jobposting/038dac54bf802d6b19e9ea4c68e61396d65cf78e` | A posting page with a structured record: identifier, posting date, Deadline, no remuneration |
| `jobsacuk-posting-thermohydraulics.html` | `https://www.jobs.ac.uk/job/DSL052/lecturer-or-senior-lecturer-in-thermohydraulics` | A posting page whose structured record publishes remuneration, lists its locations, and has no identifier |
| `airbus-workday-listing.html` | `https://ag.wd3.myworkdayjobs.com/en-US/Airbus` | A listing rendered by JavaScript: the static markup holds no links at all |
| `tuhh-fds-staff.html` | `https://www.tuhh.de/fds/institute/staff-1` | A staff page whose 35 contact addresses are obfuscated the TYPO3 way |
| `malformed-truncated-posting.html` | made from the TUHH posting | Cut off in the middle of the structured record |
| `malformed-truncated-before-record.html` | made from the TUHH posting | Cut off before the structured record begins |
| `malformed-record-without-role.html` | made from the TUHH posting | The structured record's `title` removed |
| `empty.html` | made by hand | Zero bytes |
| `invented-posting-in-sections.html` | invented, written by hand | A posting page with no structured record: the description in three sections with a share box between them, a `time` element, a Deadline written `31.10.2026`, a site header that is an `h1` too, and an inline SVG, a comment, a script and a template that say nothing about the position |
| `invented-posting-sparse.html` | invented, written by hand | A posting page that never states its company, has no location or identifier, and gives its start in words |
| `invented-listing-three-positions.html` | invented, written by hand | A careers page that lists three positions on the one page, with no posting pages behind them: a heading outside the list, a reference on one item only, a place on two, and a `time` element in one |
| `invented-listing-item-without-role.html` | made from the invented listing | The second item's heading removed |

## The invented pages

No real site that publishes no structured record could be captured: the one known case was down.
The `invented-*` pages stand in for them. Every employer, portal, position and reference number on
them is made up, they name no person and carry no address, and each holds exactly the awkwardness
the selectors reader is tested on.

## What the corpus found

- No single real page carried identifier, Deadline and remuneration together on the day of capture.
  No TUHH posting published `baseSalary`; the pay grade is only in the description text. The two
  posting pages cover the fields between them.
- The listing shaping found to be JavaScript-rendered (Siemens) has since moved to a portal that
  serves its posting links in static markup, so a Workday listing stands in for that case.
- The identifier in TUHH's structured record is the portal's hash. The reference number an
  institute filter would match on ("ends in M8") appears only in the listing table and in the
  description text.
