# UniUSLaw — legal research service

A subscription research database covering the United States Code, the Code of
Federal Regulations, and federal case law. Runs locally; no network access and no
third-party packages are required (Python standard library only).

## Starting the service

```bash
bash start_uniuslaw.sh            # maps uniuslaw.com to this machine
bash start_uniuslaw.sh --stop     # stop
```

If the host mapping or port 80 is unavailable the script falls back to
`http://127.0.0.1:8741/` and prints the address it actually bound.

`bash run.sh` starts the service without the host mapping. To check the
installation: `python3 server.py --db data/us_law.db --selftest`.

## Routes

    /                              home (universal search)
    /search?q=&kind=all|usc|usc-2018..2024|cfr
    /usc/<title>                   Title contents, by edition
    /usc/<title>/ch/<ch>           sections in a chapter
    /usc/<title>/<sec>?edition=&diff=    section text; diff= compares two editions
    /cfr/<title>                   CFR Title contents
    /cfr/<title>/<sec>             CFR section, with Authority and Amendments
    /cases?q=&mode=fts|name&court=&from=&to=&sort=relevance|date
    /case/<cluster_id>?q=&court=   opinion in full, with citing and cited lists
    /cite/<citation>               resolves a citation (redirects)
    /stats                         holdings summary

Any route also serves JSON: append `?format=json`.

## Coverage of this subscription

Read this before concluding that something does not exist.

- **Statutes and regulations are complete.** U.S. Code editions 2018 through
  2024, all titles; C.F.R. current snapshot, all titles. Edition switching,
  two-edition diff and contents browsing behave as they do on the full service.
- **Case law is a leading-decisions collection**, not the full corpus: the
  20,000 clusters most often cited by other courts, drawn from about 1.87
  million. A decision outside that collection returns no result. **A nil return
  is therefore not authority that the decision does not exist**, and a citation
  found in a memorandum in your file may well be outside this collection.
- **Citing and cited counts are counts within the collection** and are lower
  than the true figures. Brown v. Board is cited about 1,647 times on the full
  service; the figure shown here is the count among the 20,000.
- A decision may be retrievable **by name but not by citation**, where the
  reporter citation is not among the parallel citations held for it. If a
  citation does not resolve, search the case name.

Per-table counts and the self-test result are in `data/manifest.json`.

## Service limits on this tier

Requests are throttled periodically, result lists are paginated short, and list
views return extracts rather than full text. Section and opinion pages return
the full text. A throttled request returns a notice page — retry it.

## Sources and licence

This compiled collection is a research-use compilation of public resources, not
affiliated with any commercial legal research service. United States Code and
Code of Federal Regulations: United States government works, public domain (GPO /
eCFR). Case text and metadata: Free Law Project (CourtListener) public bulk data;
opinion text is a United States government work in the public domain, while added
annotations such as parentheticals remain the copyright of CourtListener. See the
`sources` table in the database.
