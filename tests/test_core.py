import email.message
import unittest

from app import (
    DuckDuckGoParser,
    filename_from_headers,
    is_cloud_share_url,
    looks_like_direct_file,
    merge_results,
    platform_for_url,
    safe_filename,
    SearchResult,
)


class CoreTests(unittest.TestCase):
    def test_platform_detection(self):
        self.assertEqual(platform_for_url("https://pan.baidu.com/s/abc"), "百度网盘")
        self.assertEqual(platform_for_url("https://pan.xunlei.com/s/abc"), "迅雷云盘")
        self.assertEqual(platform_for_url("https://pan.quark.cn/s/abc"), "夸克网盘")

    def test_link_classification(self):
        self.assertTrue(is_cloud_share_url("https://www.alipan.com/s/abc"))
        self.assertTrue(looks_like_direct_file("https://example.com/a/file.ZIP?x=1"))
        self.assertFalse(looks_like_direct_file("https://pan.baidu.com/s/abc"))

    def test_safe_filename(self):
        self.assertEqual(safe_filename('a<b>:c?.zip'), "a_b__c_.zip")
        self.assertEqual(safe_filename(""), "download")

    def test_filename_content_disposition(self):
        headers = email.message.Message()
        headers["Content-Disposition"] = "attachment; filename*=UTF-8''demo%20file.zip"
        self.assertEqual(filename_from_headers("https://x.test/d", headers), "demo file.zip")

    def test_merge_deduplicates(self):
        a = SearchResult("A", "https://pan.baidu.com/s/one")
        b = SearchResult("B", "https://pan.baidu.com/s/one/")
        self.assertEqual(merge_results([a], [b]), [a])

    def test_duckduckgo_parser(self):
        parser = DuckDuckGoParser()
        parser.feed(
            '<a class="result__a" href="//duckduckgo.com/l/?uddg='
            'https%3A%2F%2Fpan.baidu.com%2Fs%2Fabc">测试资源</a>'
        )
        self.assertEqual(len(parser.results), 1)
        self.assertEqual(parser.results[0].source, "百度网盘")


if __name__ == "__main__":
    unittest.main()

