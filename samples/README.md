# Sample documents

Both files contain **deliberately flawed requirements** so the testability scoring has
something real to catch.

| File | System | Use it for |
|---|---|---|
| `sample_srs.txt` | QuickBite food delivery | The **Load sample SRS** button in the app |
| `CampusConnect_SRS.docx` | CampusConnect library portal | Testing the **Upload file** tab |

### Flaws planted in them

| Flaw | Example | Should be caught as |
|---|---|---|
| Vague quality words | *"The app should be fast."* | Unambiguous ↓, Verifiable ↓ |
| Weak modal verbs | *"Students **may** cancel a reservation."* | Unambiguous ↓ |
| Placeholder | *"Reservation limits per student are TBD."* | Complete ↓, Verifiable ↓ |
| Bundled obligations | *"...reserve a book, notify them by email, **and** update the catalogue count."* | Atomic ↓ |
| Open-ended phrasing | *"Fines should be waived in special cases **as appropriate**."* | Complete ↓ |
| NFR with no measurable target | *"The system should be secure."* | Verifiable ↓ |
| Direct conflict | *"The cart may hold items from more than one restaurant."* vs *"An order can only contain items from a single restaurant."* | Consistent ↓ (Issues tab) |

Each file also contains well-formed requirements (*"Search results shall be returned within
1.5 seconds for 90% of queries."*) that should score 9–10, so you can confirm the scorer isn't
simply marking everything down.
