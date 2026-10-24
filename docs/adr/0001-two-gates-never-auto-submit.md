# Two gates, and the system never submits

The system automates discovery and assembly but stops one click short of sending. A Posting becomes
an Application only when a human ticks it (the Pursue Gate), and a finished Packet is turned into a
Draft rather than sent (the Send Gate). For the Portal Channel that means the form is filled and the
submit button is left alone; for the Email Channel it means an `.eml` file is written and never
handed to an SMTP server.

## Why

Full auto-submission was considered and rejected on three grounds. It is unrecoverable: a wrong
salary figure or the wrong city cannot be retracted, and the cost is a burned employer relationship
rather than a failed job. It is unreliable: portals use CAPTCHAs, session expiry, multi-step uploads
and inconsistent field names. And it sits in the same terms-of-service territory as the scraping this
project already avoids.

The failure it guards against is concrete. A past application to valantic was very nearly submitted
with the salary field defaulted to EUR instead of CHF, which would have silently understated the ask
by roughly 18,000. A human glance caught it. That glance is the feature.

## The invariant

**No code path in this system sends anything to an employer.** The Send Gate produces artifacts; a
human transmits them. This is worth stating as an invariant rather than a default, because it is the
kind of thing that erodes one convenience at a time.

## Known future exception

Replying to correspondence (a recruiter's email, a scheduling request) is a different activity from
submitting an application: lower stakes, reversible, and conversational. Agent-sent replies are
anticipated as a later feature and would be a deliberate, scoped relaxation of the invariant above.
If that is built, it must be a separate Channel with its own gate, and it must not become a route by
which a Packet can be sent.
