"""DE-8: a training set that holds a frozen test site fails.

The done-when's second clause. The draw is in the database
(`tests/integration/test_frozen_test_sites.py`); this is the refusal on the `ml/`
side, run on the file the export writes.
"""
import json

import pytest

from ml.modality.splits import (
    TestSiteInTraining,
    assert_no_test_sites,
    load_test_sites,
    split_on_test_sites,
)


@pytest.fixture
def test_sites(tmp_path):
    path = tmp_path / "test_sites.json"
    path.write_text(json.dumps({"test_sites": [
        {"location_id": "site-frozen", "region": "TX", "group": ["body_shop", "suburb"]},
    ]}))
    return load_test_sites(path)


def test_a_training_set_holding_a_test_site_fails(test_sites):
    training = [{"location_id": "site-a"}, {"location_id": "site-frozen"}]

    with pytest.raises(TestSiteInTraining, match="1 frozen test site"):
        assert_no_test_sites(training, test_sites)


def test_the_split_sends_every_record_at_a_test_site_to_test(test_sites):
    records = [
        {"location_id": "site-a"},
        {"location_id": "site-frozen"},
        {"location_id": "site-frozen"},
        {"location_id": None},
    ]

    training, test = split_on_test_sites(records, test_sites)

    assert [r["location_id"] for r in test] == ["site-frozen", "site-frozen"]
    # A record with no site cannot be at a test site, so it trains.
    assert [r["location_id"] for r in training] == ["site-a", None]
