# Jobs

A personal job-search system. It watches places where positions appear, proposes them, helps assemble
tailored application documents, and records exactly what was sent to whom. Two human gates are
mandatory: one to pursue a Posting, one to send a Packet. The system never submits on its own.

Identifiers, code and filenames are English. German appears only inside document content.

## Language

### The pipeline

**Source**:
A URL registered to be watched, together with the extraction config derived from it on first
registration. A Source may need several hops to reach the actual postings.
_Avoid_: feed, scraper, board, integration

**Registration**:
One attempt to register a Source, from handing in the URL to its end. It is *waiting* for its turn,
*under way*, *refused* with a reason, or *registered* with the Source it ended in, and it carries
what was entered. Explored in the background, one at a time, in the order handed in; until it has
ended it claims the name its Source would get, which counts as taken. *Refused* means that no Source
came of it and nothing was written, so it can be tried again as it was entered; whenever a Source
was written it is *registered*. A refused one stays until it is *cleared*, which records nothing and
decides nothing, unlike a Proposal's dismissal. It is not a Source and is not stored: what it leaves
behind is the Source file and nothing else.
_Avoid_: job, task, request, import; dismiss, for clearing one

**Strategy**:
How the pages a Source's hop chain ends at are read into Postings: from the *structured record* a
page publishes, or by *selectors* kept in the Source config. Chosen at registration by trying them in
that order, and fixed from then on: a poll runs the one its config names. A model is never a
Strategy. It may propose selectors at registration, which are kept only once they have read every
page the exploration reached.
_Avoid_: reader, parser, scraper; render mode, which is how the bytes are obtained

**Render mode**:
How the bytes of one page of a Source are obtained: `static`, a plain fetch, or `browser`, for a
page that fills in its postings with a script. A property of the fetch, recorded for each hop and for the pages the
hop chain ends at, never a way of reading a page.
_Avoid_: strategy, JS mode, headless

**Poll**:
One walk of a Source from its registered URL to its posting pages, keeping what it finds. Runs on a
schedule or when asked for by hand, and never asks a model.
_Avoid_: scrape, crawl, sync, refresh

**Health**:
What a Source's polls say about whether it still works, shown wherever Sources are. *Healthy* when
the last poll found Postings, *quiet* when the last polls found none but not yet as many in a row as
the Source's config allows, *unhealthy* from then on, and at once when a page could not be fetched,
a page could not be read by the Source's selectors, or the poll failed outright.
A poll that goes well again clears it.
_Avoid_: status, state, broken, alive

**Re-derivation**:
Exploring a Source's URL again, as registration did, because its config no longer reaches its
Postings. Only ever asked for by a human, and shown as a difference against the file as it is, which
takes effect when accepted and not before.
_Avoid_: repair, refresh, re-registration, auto-heal

**Provenance**:
What a Source config records about its own derivation: when, by which model, against which config
schema version.
_Avoid_: metadata, audit

**Posting**:
A position observed at a Source, or *brought in by hand* from the applicant's own browser. Its
*identity* is stable: re-fetching the same position next week yields the same Posting, not a new
one, and its content is refreshed in place. A Posting may have no URL, when it stands for an
employer worth an unsolicited approach rather than an advertised opening. One brought in by hand
belongs to no Source and is never polled, proposed or aged out; it is fixed as it was brought in,
except that one brought in without its text may be given it later, once.
_Avoid_: job, offering, vacancy, position, Stelle, lead, opportunity

**Application**:
A folder, its Documents and a status history, representing one pursuit of one employer. It holds at
most one Posting: created at the Pursue Gate it holds the Posting that was ticked, created by hand
it holds the Posting brought in with it, and an unsolicited approach written by hand has none. This is what the dashboard lists.
_Avoid_: bewerbung, submission, case

**Packet**:
The exact set of files sent in one go. Packet shape is a property of the receiving channel, not of
the Application: one portal wants three separate uploads, another wants a single merged PDF.
_Avoid_: bundle, attachment set, documents

**Submission**:
One act of sending a Packet. Carries a timestamp, a channel, and the frozen Manifest. An Application
can have several, because the same employer can be applied to more than once.
_Avoid_: apply, send, delivery

### Documents and proof

**Document**:
One file belonging to an Application. Shared Documents (resume, Zeugnisse, profile, photo) are stored
once by content hash and referenced; Documents unique to an Application (its Letter) are stored in
its folder.
_Avoid_: file, attachment, asset

**Letter**:
The cover letter of an Application, the one Document written for it alone. An Application has at
most one. A model only ever writes its *body*, from the subject line through the closing formula;
what is fact — who it is from, who it goes to, the date — is not prose and is not the model's to
change. It fits on one A4 page, which keeps its body to about 250 words. It can be revised only
while its Application is `todo`.
_Avoid_: cover letter, Anschreiben, draft, which is what the Send Gate produces

**Revision**:
A change to a Letter proposed by a model, shown as a difference against the Letter as it stands,
which takes effect when accepted and not before. A first version written by a model is a Revision
like any other, against a Letter with nothing in it. Only ever asked for by a human. All of it is
accepted or none of it, and a Letter has at most one waiting to be decided: asking again while one
waits revises that one. It ends by being accepted, by being *rejected*, which the model is told, or
by the Letter being changed by hand underneath it.
_Avoid_: edit, suggestion, draft, version

**Conversation**:
The messages between the applicant and the model about one Letter, kept with it. An answer is a
message and may carry a Revision; it need not, since a model that lacks a fact should ask for it.
An answer whose body took the place of an earlier one's while the Revision waited leaves that
earlier answer marked *revised*: the Revision is the same one, and waits on.
_Avoid_: chat, thread, session, history

**Brief**:
What the applicant has written down about themselves for every Letter: their voice, what they want,
and what they never claim. Written by hand, one for all Letters, and read by the model each time it
is asked.
_Avoid_: profile, which is a shared Document and also what relevance is scored against; persona,
style guide, prompt

**Manifest**:
The record written at the moment of Submission, mapping every filename in the Packet to the hash of
its content at that instant. It is what makes "what exactly did I send them" answerable later.
_Avoid_: log, receipt, index

**Freeze**:
The act of hashing a Packet and writing its Manifest at Submission. Before Freeze, Documents are
editable; after it, the Manifest is the authority on what was sent.
_Avoid_: lock, commit, seal

### Decisions

**Gate**:
A point where the system stops and waits for a human. There are exactly two: the Pursue Gate and the
Send Gate.
_Avoid_: approval, checkpoint, review step

**Pursue Gate**:
The first gate. A proposed Posting becomes an Application only when it is ticked. A Posting left
unticked ages out, on a clock that starts when it was first shown rather than when it was first
found.
_Avoid_: triage, screening, shortlist

**Proposal**:
A kept Posting offered at the Pursue Gate, with the reason it is offered. A Posting is *shown* the
first time it appears in the proposal list; a daily cap, when one is set, limits how many are shown
for the first time on one day, and one it holds back has not been shown. A Proposal ends by being ticked, by a
*dismissal*, which is a recorded decision, or by ageing out, which is not.
_Avoid_: suggestion, match, recommendation, candidate

**Send Gate**:
The second gate. Documents are complete and revised; passing this gate lets the system fill the
portal form or draft the email, and stop. The final submit is always a human click.
_Avoid_: approval, sign-off, confirm

### Sending

**Channel**:
How a Packet reaches an employer. Either Portal or Email. The Channel determines Packet shape: one
portal wants three separate uploads, another wants a single merged PDF, an Email wants attachments.
_Avoid_: method, route, medium

**Draft**:
The ready-to-send artifact the Send Gate produces. An `.eml` file for the Email Channel, a filled-in
form for the Portal Channel. A Draft is never sent by the system; a human sends it.
_Avoid_: outbox, pending send, queued message

**Deadline**:
The date after which a Posting no longer accepts applications, taken from the Posting when published.
_Avoid_: expiry, closing date, Frist

**Status**:
Where an Application currently stands. One of: `todo` (passed the Pursue Gate, not yet sent),
`waiting` (submitted, no answer), `interview` (in conversation), `closed` (rejected, withdrawn or
declined).
_Avoid_: state, stage, past
