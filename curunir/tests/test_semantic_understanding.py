

def test_anchor_position_is_the_producing_occurrence_not_the_first(tmp_path):
    """Second review, method note on finding 4: when the observed value occurs
    earlier in the document than the text that produced the observation, the
    anchor must point at the producing occurrence — position, not just content."""
    pipeline = make_pipeline(tmp_path)
    body = (b"<html><head><title>Acme Holding</title><meta charset=\"utf-8\"></head><body>"
            b"<h1>Acme Holding</h1><p>Quarterly notes and unrelated text.</p>"
            b"<footer>Published by Acme Holding</footer></body></html>")
    plant_manifestation(pipeline, source_id="live-web", native_id="https://acme.example/",
                        body=body, media_type="text/html",
                        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_new_evidence()
    store = pipeline.store
    from curunir_semantic.normalize import load_text
    document = store.records_of("semantic_document")[-1]
    text = load_text(store, document)
    publisher = [o for o in store.records_of("semantic_observation")
                 if o["attribute"] == "EXPLICIT_PUBLISHER_LINE"]
    assert publisher, "the publisher line must be observed"
    anchor = publisher[0]["anchors"][0]
    assert anchor["kind"] == "TEXT_SPAN"
    assert text[anchor["start"]:anchor["end"]] == anchor["exact_value"]
    # the value "Acme Holding" first occurs in the heading near offset 0; the
    # observation came from the footer's "Published by" line — the anchor must
    # sit inside that producing region, not at the first occurrence
    published_by = text.find("Published by")
    assert published_by >= 0
    assert anchor["start"] >= published_by, \
        f"anchor at {anchor['start']} points at an occurrence that did not produce it"
