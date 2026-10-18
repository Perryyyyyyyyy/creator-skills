import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import video_breakdown as v


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        v.configure_storage()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=v.RUNTIME / 'tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_video(self, path, color='red', audio=False):
        args = [v.binary('ffmpeg'), '-y', '-v', 'error', '-f', 'lavfi', '-i', f'color={color}:s=160x240:r=10:d=2']
        if audio:
            args += ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=2']
        args += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p']
        if audio:
            args += ['-c:a', 'aac', '-shortest']
        args += [path]
        v.command(args)

    def test_token_stripping_and_invalid_url(self):
        self.assertEqual(v.clean_url('https://www.xiaohongshu.com/explore/abc?xsec_token=SECRET#part'),
                         'https://www.xiaohongshu.com/explore/abc')
        for bad in ['file:///C:/secret', 'https://user:pass@example.com/', 'http://example.com/']:
            with self.assertRaises(ValueError):
                v.clean_url(bad)

    def test_silent_video_export_resume_and_invalidation(self):
        source = self.root / 'silent.mp4'
        self.make_video(source)
        job = v.Job(self.root / 'job')
        video = v.probe(job, source)
        self.assertFalse(job.data['media']['has_audio'])
        v.transcribe(job, video, 'small', 'zh', offline=True)
        self.assertEqual(job.data['stages']['transcription']['status'], 'skipped')
        self.assertEqual(job.data['transcript_segments'], [])
        v.frames(job, video, 15)
        original_frames = [e for e in job.data['evidence'] if e['type'] == 'frame']
        v.export(job)
        self.assertFalse((job.dir / 'output/字幕.srt').exists())
        self.assertEqual(job.data['status'], 'partial')
        resumed = v.Job(job.dir)
        v.probe(resumed, source)
        v.frames(resumed, video, 15)
        self.assertEqual(original_frames, [e for e in resumed.data['evidence'] if e['type'] == 'frame'])
        replaced = self.root / 'replacement.mp4'
        self.make_video(replaced, color='blue')
        v.probe(resumed, replaced)
        self.assertEqual(resumed.data['stages']['visual']['status'], 'pending')
        self.assertEqual(resumed.data['evidence'], [])
        self.assertFalse((resumed.dir / 'output').exists())
        self.assertTrue(list(resumed.dir.glob('previous-output-*')))

    def test_review_validation_and_csv_formula_safety(self):
        source = self.root / 'audio.mp4'
        self.make_video(source, audio=True)
        job = v.Job(self.root / 'job')
        video = v.probe(job, source)
        v.frames(job, video, 15)
        review = dict(media_sha256='wrong', reviewed_frame_ids=['f0001'], visual_segments=[], analysis=[])
        path = self.root / 'review.json'
        v.write_json(path, review)
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            v.apply_review(job, path)
        review['media_sha256'] = job.data['media']['sha256']
        review['visual_segments'] = [dict(id='s1', start_seconds=0, end_seconds=2,
            frame_ids=['f0001'], transcript_ids=[], shot_description='=DANGEROUS()',
            boundary_precision='sampled_interval', narrative_role='测试色块')]
        v.write_json(path, review)
        v.apply_review(job, path)
        v.export(job)
        csv = (job.dir / 'output/分镜.csv').read_text(encoding='utf-8-sig')
        self.assertIn("'=DANGEROUS()", csv)
        self.assertEqual(job.data['status'], 'partial')
        self.assertEqual(job.data['stages']['analysis']['status'], 'blocked')

    def test_srt_rounding_and_missing_metadata(self):
        self.assertEqual(v.timestamp(59.9996, True), '00:01:00,000')
        self.assertEqual(v.timestamp(3661.25), '01:01:01.250')
        job = v.Job(self.root / 'job')
        v.export(job)
        report = (job.dir / 'output/拆解报告.md').read_text(encoding='utf-8')
        self.assertIn('部分完成', report)
        self.assertNotIn('完整拆解已生成', report)

    def test_interruption_retains_checkpoint_parameters(self):
        job = v.Job(self.root / 'job')
        options = {'model': 'small', 'language': 'zh'}
        job.stage('transcription', 'running', options=options)
        job.data['transcript_segments'] = [{'id':'t0001', 'start_seconds':0, 'end_seconds':2,
                                           'raw_text':'test', 'edited_text':'test'}]
        job.stage('transcription', 'blocked', 'simulated interruption')
        resumed = v.Job(job.dir)
        self.assertEqual(resumed.data['stages']['transcription']['options'], options)
        self.assertEqual(resumed.data['transcript_segments'][-1]['end_seconds'], 2)

    def test_metadata_import_does_not_silently_change_source(self):
        job = v.Job(self.root / 'job')
        job.data['source_url'] = 'https://www.xiaohongshu.com/explore/first'
        data = dict(source_url='https://www.xiaohongshu.com/explore/second', observed_at=v.now(),
                    method='browser', fields={'likes': {'value': 0, 'status': 'observed'}})
        path = self.root / 'metadata.json'
        v.write_json(path, data)
        with self.assertRaises(ValueError):
            v.import_metadata(job, path)
        self.assertEqual(job.data['metadata'], {})

    def test_profile_collection_and_legacy_export_omit_unreliable_account_totals(self):
        job = v.Job(self.root / 'job')
        profile = {'user': {'userPageData': {
            'basicInfo': {'desc': '账号简介'},
            'interactions': [{'type': 'fans', 'count': '1千+'},
                             {'type': 'follows', 'count': '888'},
                             {'type': 'interaction', 'count': '9万+'}]}}}
        with patch('yt_dlp.YoutubeDL'), patch('yt_dlp.extractor.xiaohongshu.XiaoHongShuIE') as extractor:
            extractor.return_value._search_json.return_value = profile
            v.xhs_public_profile(job, 'https://www.xiaohongshu.com/user/profile/example')
        self.assertEqual(set(job.data['metadata']), {'followers', 'bio'})
        self.assertEqual(job.data['metadata']['followers']['value'], '1千+')
        job.data['metadata']['following'] = v.metadata_field('LEGACY_FOLLOWING', 'old')
        job.data['metadata']['account_likes_and_saves'] = v.metadata_field('LEGACY_ACCOUNT_TOTAL', 'old')
        v.export(job)
        report = (job.dir / 'output/拆解报告.md').read_text(encoding='utf-8')
        self.assertNotIn('LEGACY_FOLLOWING', report)
        self.assertNotIn('LEGACY_ACCOUNT_TOTAL', report)
        self.assertIn('1千+', report)

    def test_metadata_import_excludes_account_totals_but_keeps_post_interactions(self):
        job = v.Job(self.root / 'job')
        data = dict(source_url='https://www.xiaohongshu.com/explore/example', observed_at=v.now(),
                    method='browser', fields={
                        'following': {'value': 888, 'status': 'observed'},
                        'account_likes_and_saves': {'value': '9万+', 'status': 'observed'},
                        'likes': {'value': 12, 'status': 'observed'},
                        'saves': {'value': 34, 'status': 'observed'}})
        path = self.root / 'metadata.json'
        v.write_json(path, data)
        v.import_metadata(job, path)
        self.assertEqual(set(job.data['metadata']), {'likes', 'saves'})
        self.assertEqual(job.data['metadata']['likes']['value'], 12)
        self.assertEqual(set(job.data['evidence'][-1]['fields']), {'likes', 'saves'})


if __name__ == '__main__':
    unittest.main()
