"""US market plane.

The fundamentals half is the strong one, and it is free. SEC XBRL is bitemporal
by construction — every fact carries the accession that reported it and the date
that accession was `filed` — so a restatement arrives as a new fact with a later
filing date instead of overwriting the number the market actually had. That is
the same semantics the CVM side already relies on, obtained here without any
version-tracking work. And it is US-government public domain, so unlike B3/CVM it
carries no redistribution question (risk 1 in spec/00-overview.md).

CIKs that stop filing stay in the bulk dump, so the company that went bankrupt in
2009 is still there. Survivorship-free without doing anything.

The price half is the hard one and it is not free: deep US history including the
dead costs money. See docs.
"""
