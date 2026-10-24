# Files are the database, documents are content-addressed, packets freeze at submission

Application metadata lives in a structured file inside each Application's folder rather than in a
database. Shared Documents (resume, Zeugnisse, profile, photo) are stored once under their content
hash and referenced by symlink; Documents unique to an Application stay as ordinary files in its
folder. At Submission the system hashes every file in the Packet and writes a Manifest.

## Why files

The folder-per-application convention already worked, survives the application being broken or
neglected for months, is fixable in any editor, and needs no migration story. At the current scale
(45 applications) a database buys queries nobody is asking. Combined with a private git repository as
the transport, files also give history, sync between server and laptop, conflict detection and backup
for free, which is what a database would have had to earn back.

Revisit at roughly 500 Applications, not before.

## Why content-addressed, and why freeze

Measured before deciding: 628 PDFs occupying 320 MB, of which only 159 are unique. The certificate
bundle alone appears 26 times byte-for-byte. Deduplication roughly halves the store.

The non-obvious part is that this makes provenance *stronger*, not weaker. The instinct is that a
copy proves what was sent — but a copy can be edited afterwards and nothing records that it was. A
hash cannot lie. Before the Send Gate, Documents are live and editable; at Submission the Manifest
freezes filename-to-hash, and from then on it is the authority on what the employer received.

This requires git rather than Dropbox as the transport: git stores symlinks natively, Dropbox follows
and re-copies them, which would silently undo the deduplication.
