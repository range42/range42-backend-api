from app.core.exceptions import make_validation_error_detail


def test_make_validation_error_detail():
    error = {
        "loc": ("body", "vm_id"),
        "msg": "field required",
        "type": "missing",
        "input": None,
        "ctx": None,
    }
    detail = make_validation_error_detail(error)
    assert detail["field"] == "body.vm_id"
    assert detail["msg"] == "field required"
    assert detail["type"] == "missing"


def test_make_validation_error_detail_empty_loc():
    error = {"loc": (), "msg": "error", "type": "value_error"}
    detail = make_validation_error_detail(error)
    assert detail["field"] == ""


def test_make_validation_error_detail_missing_keys():
    error = {}
    detail = make_validation_error_detail(error)
    assert detail["field"] == ""
    assert detail["msg"] == ""
    assert detail["type"] == ""
    assert detail["input"] is None
    assert detail["ctx"] is None
