# Sources are URLs with frozen configs, not per-vendor API integrations

A Source is a URL a human registered to watch. On first registration an agent explores it, works out
how to reach the actual postings and how to extract them, and writes a config that is then frozen and
human-editable. Subsequent polls run that config as cheap deterministic code with no model involved.

## Why not the obvious path

Most job tools integrate per vendor: Greenhouse, Lever, Personio, Adzuna, each with its own adapter.
That path was investigated and is narrower than it looks. The large aggregators are closed or
hostile: LinkedIn's user agreement bans automated access, Indeed's publisher API is retired, StepStone
and Xing have no public API, and the Bundesagentur terms specifically forbid building your own job
pool. Scraping libraries that wrap these put the user in breach regardless of their own licence.

Meanwhile a single mechanism covers most of the market. Google for Jobs requires schema.org
`JobPosting` JSON-LD for a listing to be indexed, so employers who want to be found publish a
structured record deliberately. Verified against TU Hamburg: a plain institute page pointed to a
central portal whose posting pages emit complete JSON-LD with title, identifier, dates, deadline,
location and remuneration. No vendor adapter was needed, and TUHH runs on an ATS that was not even on
the candidate list.

URL-first also reaches what no aggregator does: the small employer with a careers page and no job
board presence, and the university institute that invites unsolicited applications to a named
professor.

## Why the config is frozen rather than re-derived

Letting a model re-read the page on every poll is slow, costs money per run, and is
non-deterministic: the same page could yield different results twice. Deriving once and freezing
turns polling into ordinary code. When a site changes and extraction breaks, the agent re-derives and
presents a diff.

## Consequence

Silent breakage is what kills scrapers. A Source returning zero Postings across consecutive runs must
be flagged unhealthy — a field on the Source, visible wherever Sources are shown — rather than be
treated as "nothing new today". Notifying about that flag is a later concern; being able to see it is
not. Some listing pages are
JavaScript-rendered (verified: Siemens) and need a headless browser in the container as a fallback.
