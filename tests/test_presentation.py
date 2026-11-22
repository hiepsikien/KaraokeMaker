import unittest

from karaoke.presentation import (
    interlude_ranges,
    normalize_overlay_effects,
    normalize_plan_objects,
    normalize_presentation,
    normalize_typography,
    normalize_waveform,
    strip_display_labels,
)


class PresentationTests(unittest.TestCase):
    def test_defaults_are_backward_compatible(self):
        data = normalize_presentation(None)
        self.assertFalse(data["interlude"]["enabled"])
        self.assertEqual(data["interlude"]["mode"], "strip")
        self.assertEqual(data["cover"]["bg_darken"], 58)
        self.assertEqual(data["cover"]["veil"], 70)
        self.assertFalse(normalize_waveform(None)["enabled"])

    def test_clamps_and_keeps_zero(self):
        typo = normalize_typography({"row_gap": 0, "lyric_size": 2, "bar_opacity": 999})
        self.assertEqual(typo["row_gap"], 0)
        self.assertEqual(typo["lyric_size"], 12)
        self.assertEqual(typo["bar_opacity"], 255)

    def test_interlude_only_for_long_gaps(self):
        lines = [
            {"text": "a", "start": 0.0, "end": 2.0},
            {"text": "b", "start": 6.0, "end": 8.0},
            {"text": "c", "start": 20.0, "end": 22.0},
        ]
        presentation = {"interlude": {"enabled": True, "min_gap": 8}}
        ranges = interlude_ranges(lines, presentation)
        self.assertEqual(len(ranges), 1)
        self.assertAlmostEqual(ranges[0]["start"], 8.3)
        self.assertAlmostEqual(ranges[0]["end"], 15.0)

    def test_interlude_off_by_default(self):
        lines = [
            {"text": "a", "start": 0.0, "end": 2.0},
            {"text": "b", "start": 20.0, "end": 22.0},
        ]
        self.assertEqual(interlude_ranges(lines, {}), [])

    def test_overlay_normalization_unique_and_clamped(self):
        specs = normalize_overlay_effects(
            [
                {"kind": "snow", "enabled": True, "params": {"intensity": 999}},
                {"kind": "snow", "enabled": True},
                {"kind": "bokeh", "params": {"palette": "neon"}},
                {"kind": "nope"},
            ]
        )
        self.assertEqual([item["kind"] for item in specs], ["snow", "bokeh"])
        self.assertEqual(specs[0]["params"]["intensity"], 200)
        self.assertEqual(specs[1]["params"]["palette"], "neon")

    def test_display_labels_are_reading_order_and_unique(self):
        plan = {
            "objects": [
                {"id": "b", "label": "flag", "box": {"x1": 0.5, "y1": 0.5, "x2": 0.7, "y2": 0.7}},
                {"id": "a", "label": "flag", "box": {"x1": 0.1, "y1": 0.1, "x2": 0.3, "y2": 0.3}},
                {"id": "c", "label": "guitar", "box": {"x1": 0.2, "y1": 0.8, "x2": 0.4, "y2": 0.9}},
            ],
            "protect": [{"label": "face", "box": {}}, {"label": "face", "box": {}}],
        }
        result = normalize_plan_objects(plan)
        labels = {item["id"]: item["display_label"] for item in result["objects"]}
        self.assertEqual(labels["a"], "flag_1")
        self.assertEqual(labels["b"], "flag_2")
        self.assertEqual(labels["c"], "guitar")
        self.assertEqual([item["label"] for item in result["protect"]], ["face", "face_2"])

    def test_strip_display_labels_is_recursive(self):
        plan = {
            "objects": [{"id": "a", "display_label": "a_1", "group_id": "tree_1"}],
            "inventory": {"objects": [{"id": "b", "display_label": "b_1"}]},
            "items": [{"objects": [{"id": "c", "display_label": "c_1"}]}],
        }
        stripped = strip_display_labels(plan)
        self.assertNotIn("display_label", stripped["objects"][0])
        self.assertNotIn("display_label", stripped["inventory"]["objects"][0])
        self.assertNotIn("display_label", stripped["items"][0]["objects"][0])
        self.assertEqual(stripped["objects"][0]["group_id"], "tree_1")


    def test_project_roundtrip_keeps_new_fields(self):
        from karaoke.project import project_from_fields, public_project

        project = project_from_fields(
            {
                "overlay_effects": [{"kind": "snow", "enabled": True}],
                "waveform": {"enabled": True, "y": 0.7},
                "presentation": {"interlude": {"enabled": True}},
                "typography": {"lyric_size": 40, "bar_opacity": 120},
            },
            status="draft",
        )
        public = public_project(project)
        self.assertTrue(public["waveform"]["enabled"])
        self.assertTrue(public["presentation"]["interlude"]["enabled"])
        self.assertEqual(public["typography"]["bar_opacity"], 120)
        self.assertEqual(public["overlay_effects"][0]["kind"], "snow")


if __name__ == "__main__":
    unittest.main()
