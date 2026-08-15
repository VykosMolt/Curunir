"""V5.8.1 repair modules — structural candidate origin and evidence lineage.

Two defects closed the V5.8.1 pilot: navigation furniture entering the claim
population, and a claim standing as its own evidence.  Both are upstream of
every semantic surface, and neither can be fixed by a rule that looks only at
the text of a span.  A breadcrumb and a sentence can be the same characters; a
claim and its evidence can be the same characters.  What separates them is
where they came from, so both repairs model provenance rather than wording.
"""
