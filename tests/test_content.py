import unittest

from src.content import make_result


class ContentTests(unittest.TestCase):
    def options(self, **overrides):
        result = {"max_chars": 2000, "css_selector": None, "wait_for": None,
                  "main_content": True, "include_links": True}
        result.update(overrides)
        return result

    def test_extracts_main_content_and_resolves_links(self):
        html = """<html><head><title>Fixture</title></head><body>
        <nav>menu</nav><main><h1>Title</h1><p>Body <a href='/docs'>docs</a></p>
        <aside>noise</aside></main><script>alert(1)</script><footer>footer</footer></body></html>"""
        result = make_result("https://example.com/", "crawl4ai", html, 200,
                             "https://example.com/", self.options())
        self.assertTrue(result.success)
        self.assertIn("Body", result.markdown)
        self.assertIn("https://example.com/docs", result.markdown)
        self.assertNotIn("alert", result.markdown)
        self.assertEqual(result.title, "Fixture")
        self.assertEqual(result.summary["link_count"], 1)
        self.assertEqual(result.summary["paragraph_count"], 1)

    def test_selector_and_http_errors_are_machine_readable(self):
        html = "<html><title>Not found</title><body>missing</body></html>"
        missing = make_result("https://example.com/", "crawl4ai", html, 404,
                              "https://example.com/", self.options())
        self.assertEqual(missing.error_code, "HTTP_ERROR")
        selected = make_result("https://example.com/", "crawl4ai", html, 200,
                               "https://example.com/", self.options(css_selector="#missing"))
        self.assertEqual(selected.error_code, "SELECTOR_NOT_FOUND")

    def test_challenge_pages_are_not_reported_as_success(self):
        html = "<html><title>Just a moment...</title><body>verify you are human</body></html>"
        result = make_result("https://example.com/", "scrapling", html, 200,
                             "https://example.com/", self.options())
        self.assertEqual(result.error_code, "BLOCKED")
        self.assertFalse(result.success)

    def test_output_limit_is_strict(self):
        html = "<main>" + ("hello " * 100) + "</main>"
        result = make_result("https://example.com/", "crawl4ai", html, 200,
                             "https://example.com/", self.options(max_chars=17))
        self.assertTrue(result.success)
        self.assertEqual(len(result.markdown), 17)
        self.assertTrue(result.truncated)
