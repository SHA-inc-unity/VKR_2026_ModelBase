import frontend.components.model_analysis as model_analysis


def test_render_key_value_frame_stringifies_mixed_scalar_values(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(model_analysis.st, "info", lambda *args, **kwargs: None)

    def fake_dataframe(frame, **kwargs):
        captured["frame"] = frame.copy()
        captured["kwargs"] = kwargs

    monkeypatch.setattr(model_analysis.st, "dataframe", fake_dataframe)

    model_analysis._render_key_value_frame(
        {
            "flag": True,
            "reason": "disabled_or_non_main",
            "score": 0.125,
            "missing": None,
            "nested": {"ignored": True},
        }
    )

    frame = captured["frame"]
    assert frame["Field"].tolist() == ["flag", "reason", "score", "missing"]
    assert frame["Value"].tolist() == ["Yes", "disabled_or_non_main", "0.1250", "—"]
    assert all(isinstance(value, str) for value in frame["Value"])
    assert captured["kwargs"] == {"width": "stretch", "hide_index": True}


def test_render_model_artifacts_section_stringifies_path_values(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(model_analysis.st, "subheader", lambda *args, **kwargs: None)

    def fake_dataframe(frame, **kwargs):
        captured["frame"] = frame.copy()
        captured["kwargs"] = kwargs

    monkeypatch.setattr(model_analysis.st, "dataframe", fake_dataframe)

    model_analysis.render_model_artifacts_section(
        {
            "artifact_paths": {
                "model": "outputs/models/main.cbm",
                "persisted": False,
                "score": 0.5,
            },
            "artifacts": {},
        }
    )

    frame = captured["frame"]
    assert frame["Artifact"].tolist() == ["model", "persisted", "score"]
    assert frame["Path"].tolist() == ["outputs/models/main.cbm", "False", "0.5"]
    assert all(isinstance(value, str) for value in frame["Path"])
    assert captured["kwargs"] == {"width": "stretch", "hide_index": True}