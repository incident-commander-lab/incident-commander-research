# Traffic data: sources and licences

Checked 22 Oct 2026. Everything replayed targets only our own cluster.

| Traffic | Source | Licence / terms | What is kept |
|---|---|---|---|
| (1) Locust users | Wikipedia hourly page views (`traces/`) | CC0 | hourly view counts |
| (2) Log replay | NASA Kennedy Space Center WWW server, July 1995, https://ita.ee.lbl.gov/html/contrib/NASA-HTTP.html (Jim Dumoulin, KSC; collected by M. Arlitt and C. Williamson, U. of Saskatchewan) | "The traces may be freely redistributed." The page also asks not to analyse beyond general traffic patterns, since the raw logs keep the originating hosts | `logs/nasa-ksc-1995-07-13.json`: requests per minute by class (static, listing, detail, search, error) for 13 Jul 1995, the month's busiest day (STS-70 launch, peak 405 req/min at 09:49 local). No hosts, no paths. The 20.7 MB source file was read once and not kept |
| (3) Security replay | PayloadsAllTheThings, https://github.com/swisskyrepo/PayloadsAllTheThings | MIT (Copyright (c) 2019 Swissky); the licence notice is kept next to the payloads | selected payloads, sent only in scheduled bursts (the sealed key lists the times) |

Not used: CSIC 2010 (no licence found online) and HttpParamsDataset (MIT packaging, but its benign
values come from CSIC 2010 and its attacks from tools whose terms we did not check). For (3) the
labels come from the schedule: a request is malicious exactly when it is part of a burst we sent.
