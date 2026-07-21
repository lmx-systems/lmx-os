from app.messaging.twilio_signature import compute_signature, signature_is_valid


def test_signature_round_trips():
    token = "test-auth-token"
    url = "https://example.com/webhooks/twilio/inbound-sms"
    params = {"From": "+15555550100", "Body": "hello", "MessageSid": "SM123"}
    signature = compute_signature(token, url, params)
    assert signature_is_valid(token, url, params, signature)


def test_signature_rejects_tampered_params():
    token = "test-auth-token"
    url = "https://example.com/webhooks/twilio/inbound-sms"
    params = {"From": "+15555550100", "Body": "hello"}
    signature = compute_signature(token, url, params)
    tampered = {**params, "Body": "goodbye"}
    assert not signature_is_valid(token, url, tampered, signature)


def test_signature_rejects_wrong_token():
    url = "https://example.com/webhooks/twilio/inbound-sms"
    params = {"From": "+15555550100"}
    signature = compute_signature("token-a", url, params)
    assert not signature_is_valid("token-b", url, params, signature)


def test_signature_rejects_wrong_url():
    token = "test-auth-token"
    params = {"From": "+15555550100"}
    signature = compute_signature(token, "https://example.com/a", params)
    assert not signature_is_valid(token, "https://example.com/b", params, signature)


def test_signature_is_independent_of_dict_insertion_order():
    token = "tok"
    url = "https://example.com/hook"
    first = compute_signature(token, url, {"a": "1", "b": "2"})
    second = compute_signature(token, url, {"b": "2", "a": "1"})
    assert first == second


def test_missing_or_empty_signature_is_invalid():
    assert not signature_is_valid("tok", "https://example.com", {}, None)
    assert not signature_is_valid("tok", "https://example.com", {}, "")
