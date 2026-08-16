from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = PROJECT_ROOT / "ui" / "index.html"


class TagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


class StaticUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.parser = TagCollector()
        cls.parser.feed(cls.html)

    def values(self, tag: str, attribute: str) -> list[str | None]:
        return [
            attrs[attribute]
            for name, attrs in self.parser.tags
            if name == tag and attribute in attrs
        ]

    def tag_by_id(self, element_id: str) -> tuple[str, dict[str, str | None]]:
        return next(
            (name, attrs) for name, attrs in self.parser.tags if attrs.get("id") == element_id
        )

    def test_three_screen_and_five_band_topology_is_stable(self) -> None:
        self.assertEqual(self.values("section", "data-screen"), ["setup", "graph", "highlander"])
        self.assertEqual(
            self.values("div", "data-graph-stage"),
            ["biomarker", "hypothesis", "roi", "recruitability", "simulation"],
        )
        self.assertEqual(
            self.values("section", "data-highlander-section"),
            ["comparison", "detail", "chat"],
        )
        self.assertNotIn("Human review actions", self.html)
        self.assertNotIn("Hard constraints are separate.", self.html)
        for removed_hook in ("frontier-count", "frontier-stat", "frontier-total", "gap-total"):
            self.assertNotIn(removed_hook, self.html)

    def test_pareto_is_an_expanded_three_axis_plan_map(self) -> None:
        tag, attrs = self.tag_by_id("pareto-plot")
        self.assertEqual(tag, "svg")
        self.assertEqual(attrs.get("data-pareto-dimensions"), "roi,recruitability,simulation")
        self.assertEqual(attrs.get("viewbox"), "0 0 700 330")
        self.assertIn("Three-dimensional Pareto view:", self.html)
        self.assertIn("Each numbered point is one plan.", self.html)
        self.assertIn('var keys = ["roi", "recruitability", "simulation"]', self.html)
        self.assertIn('circle.setAttribute("data-plan-id", program.id)', self.html)
        self.assertIn("missing objective shelf · not plotted as zero", self.html)
        self.assertIn(
            "Plans with identical vectors fan slightly around their exact shared coordinate",
            self.html,
        )
        self.assertIn('stem.setAttribute("class", "pareto-cluster-stem")', self.html)
        self.assertIn('point.baseX + "," + point.baseY', self.html)
        self.assertIn(
            '.pareto-figure { display: flex; flex: 1 1 330px; min-height: 330px;',
            self.html,
        )
        self.assertNotIn("The line is a 2-D visual guide", self.html)
        self.assertNotIn("var reported = String(program.paretoStatus", self.html)

    def test_setup_hooks_and_both_ten_point_ranges_remain_visible(self) -> None:
        self.assertEqual(
            self.values("fieldset", "data-setup-input"),
            [
                "clinical-indication",
                "biomarker-range",
                "max-biomarkers",
                "max-literature-papers",
                "hypothesis-range",
                "max-hypotheses-per-biomarker",
            ],
        )
        for element_id in ("biomarker-low", "biomarker-high", "hypothesis-low", "hypothesis-high"):
            with self.subTest(element_id=element_id):
                tag, attrs = self.tag_by_id(element_id)
                self.assertEqual(tag, "input")
                self.assertEqual(attrs.get("type"), "range")
                self.assertEqual(
                    (attrs.get("min"), attrs.get("max"), attrs.get("step")), ("1", "10", "1")
                )

    def test_expanded_node_geometry_is_preserved(self) -> None:
        for declaration in (
            "--node-w: 224px;",
            "--node-h: 144px;",
            "--lane: 272px;",
            "--band: 440px;",
        ):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, self.html)

    def test_browser_transport_is_same_origin_only(self) -> None:
        meta = next(
            attrs
            for name, attrs in self.parser.tags
            if name == "meta"
            and (attrs.get("http-equiv") or "").lower() == "content-security-policy"
        )
        policy = meta.get("content") or ""
        self.assertIn("connect-src 'self'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertIsNone(re.search(r"(?:https?|wss?)://", self.html, re.IGNORECASE))

        request_literals = re.findall(r'apiFetchJSON\(\s*["\']([^"\']+)', self.html)
        self.assertGreaterEqual(len(request_literals), 3)
        self.assertTrue(
            all(path.startswith("/api/runs") for path in request_literals), request_literals
        )

    def test_static_integration_hooks_cover_create_poll_and_highlander(self) -> None:
        required_ids = (
            "run-button",
            "graph-run-id",
            "freshness",
            "progress-strip",
            "inspector",
            "readiness-state",
            "launch-highlander",
            "live-region",
        )
        for element_id in required_ids:
            with self.subTest(element_id=element_id):
                self.tag_by_id(element_id)

        for source_contract in (
            "function submitApiRun",
            "function pollRunState",
            "function applyRunState",
            'apiFetchJSON("/api/runs"',
            '"Idempotency-Key"',
            '"/state"',
            '"/highlander"',
            "outputOrigin",
            "runtimeMaturity",
            "executionStatus",
            "reasonCode",
        ):
            with self.subTest(source_contract=source_contract):
                self.assertIn(source_contract, self.html)

    def test_rejected_first_run_restores_the_first_run_label(self) -> None:
        self.assertIn("function apiRunButtonLabel()", self.html)
        self.assertIn(
            'return state.runId ? "Create another integrated run →" : "Run RA / IRAK4 profile →";',
            self.html,
        )
        self.assertIn("elements.runButton.textContent = apiRunButtonLabel();", self.html)

    def test_truth_vocabulary_cannot_collapse_fallbacks_into_live(self) -> None:
        for label in (
            "DEMO_FALLBACK",
            "CACHED",
            "NOT_RUN",
            "NOT WIRED",
            "NOT_DECISION_GRADE",
            "TIMED_OUT",
            "FAILED",
            "SKIPPED",
        ):
            with self.subTest(label=label):
                self.assertIn(label, self.html)
        self.assertRegex(self.html, r"/FALLBACK/\.test\(value\).*return [\"']fallback[\"']")
        self.assertRegex(self.html, r"/CACHED/\.test\(value\).*return [\"']cached[\"']")


if __name__ == "__main__":
    unittest.main()
