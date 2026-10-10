from prefilter import apply_block_rules

RULE = {
    "rule": "No sales roles",
    "type": "Block",
    "keywords": "sales; telemarketing",
    "active": "Yes",
}


def job(title, description=""):
    return {"job_id": "1", "title": title, "description": description}


def test_blocks_on_title_keyword():
    passed, blocked = apply_block_rules([job("Sales Executive")], [RULE])
    assert not passed
    assert "No sales roles" in blocked[0]["filtered_reason"]
    assert "'sales'" in blocked[0]["filtered_reason"]


def test_matches_whole_words_only():
    passed, blocked = apply_block_rules([job("Wholesale Buyer", "wholesales and resale")], [RULE])
    assert passed
    assert not blocked


def test_matches_phrases_in_description():
    rule = {**RULE, "keywords": "cold calling"}
    _, blocked = apply_block_rules([job("Advisor", "Heavy cold calling quotas")], [rule])
    assert blocked


def test_only_active_block_rules_apply():
    inactive = {**RULE, "active": "No"}
    lower = {**RULE, "type": "Lower"}
    passed, blocked = apply_block_rules([job("Sales Executive")], [inactive, lower])
    assert passed
    assert not blocked


def test_special_characters_in_keywords_are_literal():
    rule = {**RULE, "keywords": "c++"}
    _, blocked = apply_block_rules([job("C++ Developer")], [rule])
    assert blocked


def test_title_only_ignores_the_description():
    passed, blocked = apply_block_rules(
        [job("Advisor", "great sales team")], [RULE], title_only=True
    )
    assert passed
    assert not blocked
    _, blocked = apply_block_rules([job("Sales Advisor")], [RULE], title_only=True)
    assert blocked
