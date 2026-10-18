from pathlib import Path
import base64
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import package_report as p

ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.runtime/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_embeds_chart_preserves_transcript_and_removes_internal_comments(self):
        from PIL import Image
        chart = self.root / 'chart.png'
        Image.new('RGB', (120,80), 'white').save(chart)
        src = self.root / 'source.md'
        src.write_text('# Test\n\n![分析图](chart.png)\n\n<!-- source segments: t0001 -->\n\n## 完整逐字稿\n\n保留的文案。\n\n[附件](完整逐字稿.md)\n', encoding='utf-8')
        result = p.package(src, self.root/'reports/report.html')
        page = Path(result['html']).read_text(encoding='utf-8')
        self.assertIn('保留的文案。', page)
        self.assertIn('href="#full-transcript"', page)
        self.assertIn('alt="分析图"', page)
        self.assertEqual(result['embedded_images'], 1)
        self.assertNotIn('source segments', page)
        self.assertNotIn('chart.png', page)

    def test_does_not_embed_unapproved_local_files_or_execute_markup(self):
        outside = self.root / 'outside.png'
        outside.write_bytes(b'\x89PNG\r\n\x1a\n')
        source_dir = self.root / 'source'
        source_dir.mkdir()
        with self.assertRaises(ValueError):
            p.render('![image](../outside.png)', source_dir, [])
        page, _ = p.render('# Test\n\n<script>alert(1)</script>\n\n[link](javascript:bad)\n', source_dir, [])
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('href="javascript:', page)

    def test_real_tutorial_code_is_kept_unless_removal_is_requested(self):
        source = '# Test\n\n```python\nprint(1)\n```\n\n```internal\nSECRET_INTERNAL\n```\n'
        cleaned = p.clean_markdown(source)
        self.assertIn('print(1)', cleaned)
        self.assertNotIn('SECRET_INTERNAL', cleaned)
        self.assertNotIn('print(1)', p.clean_markdown(source, True))

    def test_no_source_overwrite(self):
        source = self.root/'same.md'
        source.write_text('# Keep source', encoding='utf-8')
        with self.assertRaises(ValueError):
            p.package(source, self.root/'same.html')
        self.assertEqual(source.read_text(encoding='utf-8'), '# Keep source')


if __name__ == '__main__':
    unittest.main()
