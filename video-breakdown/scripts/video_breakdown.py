"""Local evidence preparation and export. AI interpretation is added via review JSON."""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import importlib.metadata
import importlib
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
VERSION = '1.0.0'
TZ = timezone(timedelta(hours=8))
DIMENSIONS = ['目标人群', '选题与价值', '标题与封面', '开头钩子', '内容结构', '情绪与表达', '视听与节奏', '互动与转化']
STAGES = ['metadata', 'media', 'transcription', 'visual', 'alignment', 'analysis', 'export']
LABELS = dict(title='标题', body='笔记正文', author_name='账号名', author_profile_url='账号主页',
              note_id='笔记 ID', likes='点赞', saves='收藏', comments='评论', shares='分享', views='播放量',
              followers='粉丝量', following='关注', account_likes_and_saves='账号累计获赞与收藏',
              platform='平台', published_at='发布时间', bio='简介', account_category='账号定位')
STAGE_LABELS = dict(metadata='基础数据', media='视频获取与校验', transcription='音频转写',
                   visual='画面观察', alignment='成片还原', analysis='内容分析', export='导出')
STATUS_LABELS = dict(pending='待处理', running='进行中', completed='完成', blocked='待补充', failed='失败', skipped='跳过',
                    observed='已观察', unavailable='未取得', inferred='分析推断', approximate='近似值')


class QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def configure_storage():
    if os.name == 'nt' and ROOT.drive.upper() != 'D:':
        raise RuntimeError('Storage preference requires installation on D:.')
    paths = {
        'TEMP': RUNTIME / 'tmp', 'TMP': RUNTIME / 'tmp',
        'HF_HOME': RUNTIME / 'cache/huggingface',
        'HF_HUB_CACHE': RUNTIME / 'cache/huggingface/hub',
        'XDG_CACHE_HOME': RUNTIME / 'cache',
        'PIP_CACHE_DIR': RUNTIME / 'cache/pip',
        'TORCH_HOME': RUNTIME / 'cache/torch',
    }
    for key, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)
    os.environ.update(PYTHONDONTWRITEBYTECODE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      HF_HUB_DISABLE_XET='1', HF_HUB_DISABLE_SYMLINKS_WARNING='1')


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
        temp = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False, indent=2)
    try:
        for attempt in range(5):
            try:
                temp.replace(path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)


def clean_url(value):
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('Expected an HTTPS URL without embedded credentials.')
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def binary(name):
    for path in [Path('D:/Apps/FFmpeg/bin') / (name + '.exe'), RUNTIME / 'bin' / (name + '.exe')]:
        if path.is_file():
            return str(path)
    raise RuntimeError(f'{name} not found on D:.')


def command(argv, timeout=120):
    proc = subprocess.run([str(x) for x in argv], capture_output=True, timeout=timeout,
                          creationflags=0x08000000 if os.name == 'nt' else 0)
    if proc.returncode:
        # Never echo raw downloader errors/URLs into reports.
        raise RuntimeError(f'{Path(str(argv[0])).name} failed with exit code {proc.returncode}')
    return proc.stdout.decode('utf-8', errors='replace')


def timestamp(value, srt=False):
    ms = max(0, int(round(value * 1000)))
    hours, rest = divmod(ms, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, milli = divmod(rest, 1000)
    return f'{hours:02}:{minutes:02}:{seconds:02}{"," if srt else "."}{milli:03}'


def cell(value):
    text = '' if value is None else str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def md(value):
    return str(value if value is not None else '未取得').replace('|', '\\|').replace('\n', '<br>')


class Job:
    def __init__(self, directory):
        self.dir = Path(directory).resolve()
        if os.name == 'nt' and self.dir.drive.upper() != 'D:':
            raise ValueError('Jobs must be stored on D:.')
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / 'record.json'
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
        else:
            self.data = dict(schema_version='1.0', task_id=self.dir.name, created_at=now(),
                             updated_at=now(), input_kind=None, source_url=None, status='partial',
                             media={}, stages={k: {'status': 'pending'} for k in STAGES},
                             metadata={}, evidence=[], transcript_segments=[], visual_segments=[],
                             analysis=[], missing_items=[])

    @contextlib.contextmanager
    def lock(self):
        lock = self.dir / '.lock'
        try:
            handle = lock.open('x', encoding='utf-8')
        except FileExistsError:
            raise RuntimeError('Job is locked. If no process is running, remove only its .lock file.')
        try:
            handle.write(str(os.getpid()))
            handle.close()
            yield
        finally:
            lock.unlink(missing_ok=True)

    def save(self):
        self.data['updated_at'] = now()
        write_json(self.path, self.data)

    def stage(self, name, status, reason=None, **extra):
        previous = self.data['stages'].get(name, {})
        if 'options' in previous and 'options' not in extra:
            extra['options'] = previous['options']
        self.data['stages'][name] = dict(status=status, updated_at=now(), **extra)
        self.data['missing_items'] = [x for x in self.data['missing_items'] if x['field_or_stage'] != name]
        if reason:
            self.data['stages'][name]['reason'] = reason
        if status in ('blocked', 'failed'):
            self.data['missing_items'].append(dict(field_or_stage=name, reason=reason,
                                                  next_input='补充相应素材或运行依赖后重试'))
        self.save()
        print(f'[{name}] {status}' + (f' — {reason}' if reason else ''), flush=True)

    def evidence(self, item):
        self.data['evidence'] = [x for x in self.data['evidence'] if x['id'] != item['id']]
        self.data['evidence'].append(item)

    def reset_media_dependents(self):
        self.data.update(transcript_segments=[], visual_segments=[], analysis=[])
        self.data.pop('processed_audio_intervals', None)
        self.data['evidence'] = [e for e in self.data['evidence'] if e['type'] == 'page_snapshot']
        for stage in STAGES[2:]:
            self.data['stages'][stage] = {'status': 'pending'}
        self.data['missing_items'] = [x for x in self.data['missing_items'] if x['field_or_stage'] in ('metadata', 'media')]
        # Previous outputs remain as historical evidence; no longer list them as current.
        if (self.dir / 'output').exists():
            archive = self.dir / ('previous-output-' + datetime.now(TZ).strftime('%Y%m%d-%H%M%S-%f'))
            (self.dir / 'output').rename(archive)


def metadata_field(value, evidence_id, note=''):
    return dict(value=value, raw_display=str(value) if value is not None else None,
                status='observed' if value is not None else 'unavailable', observed_at=now(),
                evidence_ids=[evidence_id] if value is not None else [], note=note)


def xhs_public_metadata(job, url, note=None):
    """Read fields carried by the public note page. No signing, cookies or login bypass."""
    if urlsplit(url).hostname not in ('www.xiaohongshu.com', 'xiaohongshu.com'):
        return
    from yt_dlp.utils import js_to_json
    try:
        if note is None:
            from yt_dlp import YoutubeDL
            from yt_dlp.extractor.xiaohongshu import XiaoHongShuIE
            # Use the same request and JSON parser as the tested platform extractor.
            with YoutubeDL({'quiet': True, 'logger': QuietLogger(), 'socket_timeout': 25,
                            'cachedir': str(RUNTIME / 'cache/yt-dlp')}) as ydl:
                extractor = XiaoHongShuIE(ydl)
                html = extractor._download_webpage(url, 'metadata')
                initial = extractor._search_json(r'window\.__INITIAL_STATE__\s*=', html,
                            'initial state', 'metadata', transform_source=js_to_json)
            note_id = urlsplit(url).path.rstrip('/').split('/')[-1]
            note = initial.get('note', {}).get('noteDetailMap', {}).get(note_id, {}).get('note', {})
        if not note:
            return
        interaction, author = note.get('interactInfo', {}), note.get('user', {})
        mapping = dict(title=note.get('title'), body=note.get('desc'), author_name=author.get('nickname'),
                       author_profile_url=('https://www.xiaohongshu.com/user/profile/' + author['userId'])
                       if author.get('userId') else None,
                       likes=interaction.get('likedCount'), saves=interaction.get('collectedCount'),
                       comments=interaction.get('commentCount'), shares=interaction.get('shareCount'))
        observed = {}
        for name, value in mapping.items():
            if value is None:
                continue
            entry = metadata_field(value, 'page-xhs-public')
            if name in ('likes', 'saves', 'comments', 'shares'):
                raw = str(value)
                entry['raw_display'] = raw
                if re.fullmatch(r'\d+', raw):
                    entry['value'] = int(raw)
                elif re.fullmatch(r'\d+(?:\.\d+)?[万千]', raw):
                    entry.update(value=float(raw[:-1]) * (10000 if raw.endswith('万') else 1000), status='approximate')
            observed[name] = entry
        if isinstance(note.get('time'), (int, float)):
            entry = metadata_field(datetime.fromtimestamp(note['time']/1000, TZ).isoformat(), 'page-xhs-public')
            entry.update(precision='datetime', raw_display=str(note['time']), note='公开页面发布毫秒时间戳转换为北京时间')
            observed['published_at'] = entry
        job.evidence(dict(id='page-xhs-public', type='page_snapshot', source_url=clean_url(url),
                          observed_at=now(), method='公开页面初始笔记数据', fields=observed))
        job.data['metadata'].update(observed)
        job.save()
        print('[metadata] public note fields updated', flush=True)
        if author.get('userId'):
            xhs_public_profile(job, 'https://www.xiaohongshu.com/user/profile/' + author['userId'])
    except Exception as exc:
        # Supplementary metadata must not invalidate successfully acquired media.
        print('[metadata] public-page supplement unavailable: ' + type(exc).__name__, flush=True)


def xhs_public_profile(job, url):
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.xiaohongshu import XiaoHongShuIE
    from yt_dlp.utils import js_to_json
    try:
        with YoutubeDL({'quiet': True, 'logger': QuietLogger(), 'socket_timeout': 25,
                        'cachedir': str(RUNTIME / 'cache/yt-dlp')}) as ydl:
            extractor = XiaoHongShuIE(ydl)
            html = extractor._download_webpage(url, 'profile')
            initial = extractor._search_json(r'window\.__INITIAL_STATE__\s*=', html,
                        'profile state', 'profile', transform_source=js_to_json)
        profile = initial.get('user', {}).get('userPageData', {})
        basic = profile.get('basicInfo', {})
        mapping = {'fans': 'followers', 'follows': 'following', 'interaction': 'account_likes_and_saves'}
        fields = {}
        if basic.get('desc') is not None:
            fields['bio'] = metadata_field(basic['desc'], 'page-xhs-profile')
        for item in profile.get('interactions', []):
            if item.get('type') in mapping and item.get('count') is not None:
                raw = str(item['count'])
                entry = metadata_field(int(raw) if raw.isdigit() else raw, 'page-xhs-profile')
                entry['raw_display'] = raw
                if not raw.isdigit():
                    entry['note'] = '保留公开主页的区间或缩略显示，不换算成精确数字'
                fields[mapping[item['type']]] = entry
        if not fields:
            return
        job.evidence(dict(id='page-xhs-profile', type='page_snapshot', source_url=clean_url(url),
                          observed_at=now(), method='作者公开主页数据', fields=fields))
        job.data['metadata'].update(fields)
        job.save()
        print('[metadata] public profile fields updated', flush=True)
    except Exception as exc:
        print('[metadata] public profile unavailable: ' + type(exc).__name__, flush=True)


def download(job, url):
    from yt_dlp import YoutubeDL

    canonical = clean_url(url)
    if job.data['source_url'] and job.data['source_url'] != canonical:
        raise ValueError('Different source URL: create a new job to avoid mixing evidence.')
    job.data.update(source_url=canonical, input_kind='url')
    source = job.dir / 'source'
    source.mkdir(exist_ok=True)
    job.stage('media', 'running')
    opts = dict(outtmpl=str(source / 'video.%(ext)s'), noplaylist=True, quiet=True,
                logger=QuietLogger(), cachedir=str(RUNTIME / 'cache/yt-dlp'),
                ffmpeg_location=str(Path(binary('ffmpeg')).parent), socket_timeout=25,
                retries=1, fragment_retries=1, extractor_retries=1,
                format='best[ext=mp4]/best', windowsfilenames=True, nopart=False)
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
        if not path.is_file():
            raise RuntimeError('Downloaded media file not found')
        fields = dict(title=info.get('title'), body=info.get('description'),
                      author_name=info.get('uploader'), note_id=info.get('id'),
                      author_profile_url=info.get('uploader_url'), likes=info.get('like_count'),
                      saves=None,
                      comments=info.get('comment_count'), views=info.get('view_count'),
                      followers=info.get('channel_follower_count'), platform=info.get('extractor_key'),
                      published_at=info.get('upload_date'), shares=None)
        # Fields absent from yt-dlp remain null; repost_count is not a collection count.
        for name, value in fields.items():
            if name.endswith('_url') and value:
                value = clean_url(value)
            job.data['metadata'][name] = metadata_field(value, 'page-download')
        job.evidence(dict(id='page-download', type='page_snapshot', source_url=canonical,
                          observed_at=now(), method='yt-dlp metadata; no raw signed URLs saved'))
        job.stage('metadata', 'completed', missing_fields=[k for k, v in fields.items() if v is None])
        xhs_public_metadata(job, url)
        return path
    except Exception as exc:
        job.stage('media', 'blocked', '链接未取得可用视频；可补入本地视频。错误类型：' + type(exc).__name__)
        if job.data['stages']['metadata']['status'] == 'pending':
            job.stage('metadata', 'blocked', '未取得页面数据，可通过浏览器读取后导入')
        return None


def probe(job, path):
    info = json.loads(command([binary('ffprobe'), '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path]))
    duration = float(info.get('format', {}).get('duration') or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid media duration')
    streams = info.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if video is None:
        raise ValueError('Input must contain a video stream')
    fingerprint = sha256(path)
    old = job.data['media'].get('sha256')
    if old and old != fingerprint:
        job.reset_media_dependents()
    source = job.dir / 'source'
    source.mkdir(exist_ok=True)
    destination = source / ('video' + path.suffix.lower())
    if path.resolve() != destination.resolve():
        shutil.copyfile(path, destination)
    # Validate actual decode, not just metadata or a plausible URL.
    command([binary('ffmpeg'), '-v', 'error', '-i', destination, '-frames:v', '1', '-f', 'null', '-'])
    job.data['media'] = dict(path=destination.relative_to(job.dir).as_posix(), sha256=fingerprint,
                             duration_seconds=duration, has_audio=any(s.get('codec_type') == 'audio' for s in streams),
                             width=video.get('width'), height=video.get('height'), validated=True)
    job.stage('media', 'completed')
    return destination


def transcribe(job, video, model_name, language, offline=False):
    options = dict(model=model_name, language=language, device='cpu', compute_type='int8', version=VERSION)
    if (job.data['stages']['transcription'].get('status') == 'completed'
            and job.data['stages']['transcription'].get('options') == options):
        print('[transcription] reuse verified input and matching options', flush=True)
        return
    resume_words = (job.data['transcript_segments'] if
                    job.data['stages']['transcription'].get('options') == options else [])
    offset = max((x['end_seconds'] for x in resume_words), default=0.0)
    # Recomputing words invalidates interpretation, but frame observations can still be re-read.
    job.data['analysis'] = []
    job.data['visual_segments'] = []
    job.data['stages']['analysis'] = {'status': 'pending'}
    job.data['stages']['alignment'] = {'status': 'pending'}
    job.data['transcript_segments'] = resume_words
    job.data.pop('processed_audio_intervals', None)
    job.data['evidence'] = [e for e in job.data['evidence'] if e['type'] != 'audio_segment'
                            and (resume_words or e['type'] != 'transcript_segment')]
    if not job.data['media']['has_audio']:
        job.stage('transcription', 'skipped', '视频无音轨；不会生成音频逐字稿')
        return
    job.stage('transcription', 'running', options=options)
    from faster_whisper import WhisperModel
    audio = job.dir / 'audio/audio.wav'
    audio.parent.mkdir(exist_ok=True)
    command([binary('ffmpeg'), '-y', '-v', 'error', '-i', video, '-vn', '-ar', '16000', '-ac', '1', audio], timeout=600)
    job.evidence(dict(id='audio', type='audio_segment', path=audio.relative_to(job.dir).as_posix(),
                      start_seconds=0, end_seconds=job.data['media']['duration_seconds'], observed_at=now()))
    model = WhisperModel(model_name, device='cpu', compute_type='int8', cpu_threads=min(os.cpu_count() or 4, 8),
                         download_root=str(RUNTIME / 'models'), local_files_only=offline)
    # Read our normalized PCM directly: avoids decoder API differences between PyAV releases.
    import numpy as np
    with wave.open(str(audio), 'rb') as source:
        if source.getframerate() != 16000 or source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError('Expected normalized 16 kHz mono PCM16 audio')
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if offset:
        print('[transcription] resume from ' + timestamp(offset), flush=True)
    segments, info = model.transcribe(samples[int(offset*16000):], beam_size=5, language=None if language == 'auto' else language,
                                      vad_filter=True, condition_on_previous_text=False)
    duration = job.data['media']['duration_seconds']
    for segment in segments:
        start, end = max(offset, segment.start + offset), min(duration, segment.end + offset)
        if end <= start or not segment.text.strip():
            continue
        sid = f't{len(job.data["transcript_segments"])+1:04}'
        item = dict(id=sid, start_seconds=start, end_seconds=end, raw_text=segment.text.strip(),
                    edited_text=segment.text.strip(), speaker_label=None, review_status='machine_unreviewed',
                    corrections=[], evidence_ids=['audio'], engine_metrics={
                        'avg_logprob': segment.avg_logprob, 'no_speech_prob': segment.no_speech_prob})
        job.data['transcript_segments'].append(item)
        job.evidence(dict(id=sid, type='transcript_segment', start_seconds=start, end_seconds=end,
                          text=item['raw_text'], observed_at=now()))
        job.save()
        print(f'[transcription] processed speech to {timestamp(end)} / {timestamp(duration)}', flush=True)
    raw = dict(engine='faster-whisper', options=options, language=info.language,
               duration_seconds=duration, input_window_seconds=[offset, duration], segments=job.data['transcript_segments'])
    write_json(job.dir / 'transcript/raw.json', raw)
    job.data['processed_audio_intervals'] = [[0, offset], [offset, duration]] if offset else [[0, duration]]
    if offset:
        job.data.setdefault('transcription_resume_boundaries', []).append(offset)
    job.stage('transcription', 'completed', options=options,
              note='音频全长处理完成，文字为机器初稿，尚未逐句人工听校')


def frames(job, video, interval):
    if not math.isfinite(interval) or interval < 1:
        raise ValueError('Frame interval must be finite and >= 1 second')
    duration = job.data['media']['duration_seconds']
    times = {0.0, max(0.0, duration - 0.25)}
    times.update(t for t in (2.0, 5.0, 10.0) if t < duration)
    times.update(round(i * interval, 3) for i in range(1, math.ceil(duration / interval)) if i * interval < duration)
    if len(times) > 600:
        raise ValueError('Too many sampled frames; increase --interval (limit 600)')
    params = dict(interval=interval, version=VERSION)
    previous = job.data['stages']['visual']
    cached = [e for e in job.data['evidence'] if e['type'] == 'frame']
    if previous.get('options') == params and previous['status'] in ('completed', 'blocked') and cached:
        if all((job.dir / f['path']).is_file() for f in cached):
            print('[visual] reuse extracted frames' +
                  (' and saved observations' if previous['status'] == 'completed' else '; review still required'), flush=True)
            return
    job.data['evidence'] = [e for e in job.data['evidence'] if e['type'] != 'frame']
    job.data['visual_segments'], job.data['analysis'] = [], []
    job.data['stages']['analysis'] = {'status': 'pending'}
    job.data['stages']['alignment'] = {'status': 'pending'}
    job.stage('visual', 'running', options=params)
    from PIL import Image, ImageOps, ImageDraw
    folder = job.dir / 'frames'
    folder.mkdir(exist_ok=True)
    extracted = []
    for i, time in enumerate(sorted(times), 1):
        target = folder / f'frame-{i:04}.jpg'
        command([binary('ffmpeg'), '-y', '-v', 'error', '-ss', f'{time:.3f}', '-i', video,
                 '-frames:v', '1', '-vf', 'scale=1200:1200:force_original_aspect_ratio=decrease', '-q:v', '3', target])
        if not target.is_file():
            raise RuntimeError('Could not decode frame near media boundary')
        item = dict(id=f'f{i:04}', type='frame', path=target.relative_to(job.dir).as_posix(),
                    time_seconds=time, observed_at=now())
        job.evidence(item)
        extracted.append(item)
    sheets = []
    for offset in range(0, len(extracted), 16):
        batch = extracted[offset:offset+16]
        sheet = Image.new('RGB', (1200, math.ceil(len(batch)/4)*250), '#eeeeee')
        draw = ImageDraw.Draw(sheet)
        for n, item in enumerate(batch):
            with Image.open(job.dir / item['path']) as image:
                thumb = ImageOps.contain(image, (290, 218))
                x, y = n % 4 * 300, n // 4 * 250
                sheet.paste(thumb, (x + (300-thumb.width)//2, y))
                draw.text((x+6, y+224), f'{item["id"]}  {timestamp(item["time_seconds"])}', fill='black')
        path = folder / f'contact-{offset//16+1:02}.jpg'
        sheet.save(path, quality=88)
        sheets.append(path.relative_to(job.dir).as_posix())
    write_json(folder / 'index.json', extracted)
    job.stage('visual', 'blocked', '关键帧已提取，需由具备图像理解能力的助手读取并导入观察',
              options=params, frame_count=len(extracted), contact_sheets=sheets)


def apply_review(job, path):
    review = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if review.get('media_sha256') != job.data['media'].get('sha256'):
        raise ValueError('Review media fingerprint mismatch')
    duration = job.data['media']['duration_seconds']
    evidence = {e['id']: e for e in job.data['evidence']}
    segments = review.get('visual_segments', [])
    analyses = review.get('analysis', [])
    if not isinstance(segments, list) or not isinstance(analyses, list):
        raise ValueError('Review segments and analysis must be arrays')
    for item in segments:
        a, b = item['start_seconds'], item['end_seconds']
        if not (0 <= a < b <= duration):
            raise ValueError('Review segment timestamp outside media')
        ids = item.get('frame_ids', [])
        if not ids or any(k not in evidence or evidence[k]['type'] != 'frame' for k in ids):
            raise ValueError('Visual observations require existing frame evidence')
        if not any(a <= evidence[k]['time_seconds'] <= b for k in ids):
            raise ValueError('Visual observation must reference a frame in its time interval')
        if any(k not in evidence or evidence[k]['type'] != 'transcript_segment' for k in item.get('transcript_ids', [])):
            raise ValueError('Unknown transcript evidence')
        if item.get('boundary_precision') not in ('sampled_interval', 'verified_transition', 'unknown'):
            raise ValueError('Set the actual boundary precision')
    for item in analyses:
        if item.get('dimension') not in DIMENSIONS or not item.get('finding'):
            raise ValueError('Analysis dimension or finding missing')
        if item.get('claim_type') not in ('observation', 'author_claim', 'inference', 'recommendation'):
            raise ValueError('Invalid claim type')
        if not item.get('evidence_ids') or any(k not in evidence for k in item['evidence_ids']):
            raise ValueError('Analysis must cite existing evidence')
    reviewed_ids = review.get('reviewed_frame_ids', [])
    if not reviewed_ids or any(k not in evidence or evidence[k]['type'] != 'frame' for k in reviewed_ids):
        raise ValueError('Record frames actually reviewed')
    if any(k not in reviewed_ids for s in segments for k in s.get('frame_ids', [])):
        raise ValueError('Referenced frames must be included among reviewed frames')
    if review.get('summary') is not None and not isinstance(review['summary'], str):
        raise ValueError('Review summary must be text')
    job.data.update(visual_segments=segments, analysis=analyses, summary=review.get('summary'))
    options = job.data['stages']['visual'].get('options', {})
    job.stage('visual', 'completed', options=options, reviewed_frame_ids=reviewed_ids,
              note='关键帧抽样观察；不是逐帧观看')
    covered = sorted((s['start_seconds'], s['end_seconds']) for s in segments)
    cursor = 0.0
    for a, b in covered:
        if a > cursor + 0.001:
            break
        cursor = max(cursor, b)
    job.stage('alignment', 'completed' if cursor >= duration-0.001 else 'blocked',
              None if cursor >= duration-0.001 else '成片还原时间轴仍有未覆盖区间')
    missing = sorted(set(DIMENSIONS) - {a['dimension'] for a in analyses})
    job.stage('analysis', 'completed' if not missing else 'blocked',
              '未分析角度：' + '、'.join(missing) if missing else None)
    write_json(job.dir / 'review/applied.json', review)


def import_metadata(job, path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    records = data.get('fields', {})
    source_url = clean_url(data.get('source_url'))
    if job.data['source_url'] and source_url != job.data['source_url']:
        raise ValueError('Metadata source does not match this job')
    if not source_url or not data.get('observed_at') or not data.get('method'):
        raise ValueError('Metadata import requires source_url, observed_at and method')
    if datetime.fromisoformat(data['observed_at']).utcoffset() is None:
        raise ValueError('Metadata observed_at requires an explicit timezone')
    eid = 'page-' + str(len(job.data['evidence']) + 1)
    for key, value in records.items():
        if not isinstance(value, dict) or value.get('status') not in ('observed', 'approximate', 'inferred', 'unavailable'):
            raise ValueError('Imported fields require value and observation status')
        if key.endswith('_url') and value.get('value'):
            value['value'] = clean_url(value['value'])
        value.update(observed_at=data['observed_at'], evidence_ids=[eid])
    job.evidence(dict(id=eid, type='page_snapshot', source_url=source_url,
                      observed_at=data['observed_at'], method=data['method'], fields=records))
    job.data['metadata'].update(records)
    job.data['source_url'] = source_url
    job.stage('metadata', 'completed')


def export(job):
    out = job.dir / 'output'
    out.mkdir(exist_ok=True)
    d, meta = job.data, job.data['metadata']
    complete = all(d['stages'][s]['status'] == 'completed' for s in STAGES if s != 'export')
    d['status'] = 'completed' if complete else 'partial'
    files = []
    words = d['transcript_segments']
    if words:
        lines = ['# 视频音频逐字稿', '', '机器初稿，尚未逐句听校；不确定处需结合原音频复核。', '']
        subtitles = []
        for i, item in enumerate(words, 1):
            a, b, text = item['start_seconds'], item['end_seconds'], item['edited_text']
            lines.extend([f'[{item["id"]} | {timestamp(a)} → {timestamp(b)}] {text}', ''])
            subtitles.append(f'{i}\n{timestamp(a, True)} --> {timestamp(b, True)}\n{text}\n')
        (out / '逐字稿.md').write_text('\n'.join(lines), encoding='utf-8')
        (out / '字幕.srt').write_text('\n'.join(subtitles), encoding='utf-8-sig')
        files += ['逐字稿.md', '字幕.srt']
    if d['visual_segments']:
        with (out / '分镜.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['起始秒', '结束秒', '起始时间码', '结束时间码', '粒度', '画面', '动作', '口播', '屏幕文字', '声音', '剪辑', '段落作用', '证据编号'])
            for s in d['visual_segments']:
                speech = ' '.join(t['edited_text'] for t in words if t['id'] in s.get('transcript_ids', []))
                writer.writerow([s['start_seconds'], s['end_seconds'], timestamp(s['start_seconds']), timestamp(s['end_seconds']),
                                 s['boundary_precision'], *[cell(x) for x in [s.get('shot_description'), s.get('actions'), speech,
                                 s.get('screen_text'), s.get('audio_observations'), s.get('editing_observations'), s.get('narrative_role'),
                                 ','.join(s['frame_ids'] + s.get('transcript_ids', []))]]])
        files.append('分镜.csv')
    title = meta.get('title', {}).get('value') or '视频内容拆解'
    lines = [f'# {md(title)}', '', f'生成时间：{now()}  ',
             '状态：' + ('完整拆解已生成' if complete else '部分完成，详见处理状态与缺失项'), '']
    if d.get('summary'):
        lines += ['## 核心结论', '', d['summary'], '']
    lines += ['## 处理状态', '', '| 模块 | 状态 | 说明 |', '|---|---|---|']
    for name, value in d['stages'].items():
        if name != 'export':
            lines.append(f'| {STAGE_LABELS[name]} | {STATUS_LABELS[value["status"]]} | {md(value.get("reason") or value.get("note") or "")} |')
    lines += ['', '## 基础信息', '', '| 字段 | 值 | 口径 | 观察时间 |', '|---|---|---|---|']
    for name, value in meta.items():
        lines.append(f'| {md(LABELS.get(name,name))} | {md(value.get("value"))} | {STATUS_LABELS.get(value.get("status"),value.get("status"))} | {md(value.get("observed_at"))} |')
    if d['media']:
        lines += ['', f'视频时长：{d["media"]["duration_seconds"]:.3f} 秒；画幅：{d["media"]["width"]} × {d["media"]["height"]}。']
    if d['source_url']:
        lines += ['', f'来源：{d["source_url"]}']
    if meta.get('likes', {}).get('value') and isinstance(meta.get('saves', {}).get('value'), (int, float)):
        lines += ['', f'收藏与点赞比：{meta["saves"]["value"] / meta["likes"]["value"]:.2f}。该比值不是播放口径的收藏率。']
    if d['analysis']:
        lines += ['', '## 八个角度拆解', '']
        for dimension in DIMENSIONS:
            matching = [x for x in d['analysis'] if x['dimension'] == dimension]
            if matching:
                lines += [f'### {dimension}', '']
            for item in matching:
                lines += [item['finding'], '', f'判断类型：{item["claim_type"]}；证据：{", ".join(item["evidence_ids"])}。', '']
                if item.get('limitation'):
                    lines += ['范围与限制：' + item['limitation'], '']
    else:
        lines += ['', '## 内容分析', '', '尚未导入助手对原始文案和画面的分析；本文件不会根据标题自动补写全片内容。']
    if d['visual_segments']:
        lines += ['', '## 成片还原脚本', '', '以下为根据画面证据还原的段落，不是作者拍摄前的原始脚本。', '']
        for s in d['visual_segments']:
            lines += [f'### {timestamp(s["start_seconds"])} 至 {timestamp(s["end_seconds"])}', '',
                      s.get('shot_description', ''), '', '结构作用：' + s.get('narrative_role', '未判断'), '',
                      '证据：' + ', '.join(s['frame_ids'] + s.get('transcript_ids', [])), '']
    lines += ['', '## 素材与处理范围', '',
              '音频已处理范围：' + json.dumps(d.get('processed_audio_intervals', [])),
              '画面采用关键帧抽样；画面之间的动作与切换未必全部观察。',
              '互动数据只代表观察时点；没有播放量时不计算播放互动率。', '', '## 缺失项', '']
    for item in d['missing_items']:
        lines.append(f'- {item["field_or_stage"]}：{item["reason"]}')
    if not d['missing_items']:
        lines.append('无阶段阻塞；未取得的可选字段见基础信息。')
    lines += ['', '## 证据索引', '', '转写证据编号见逐字稿中的 t 编号。帧证据如下，图片采用抽样时间：', '',
              '| 编号 | 时间 | 文件 |', '|---|---|---|']
    for item in d['evidence']:
        if item['type'] == 'frame':
            lines.append(f'| {item["id"]} | {timestamp(item["time_seconds"])} | [查看](../{item["path"]}) |')
    (out / '拆解报告.md').write_text('\n'.join(lines), encoding='utf-8')
    files.append('拆解报告.md')
    d['outputs'] = ['output/' + f for f in files]
    job.stage('export', 'completed', files=d['outputs'])


def run(args):
    directory = Path(args.job) if args.job else ROOT / 'runs' / datetime.now(TZ).strftime('%Y%m%d-%H%M%S-%f')
    job = Job(directory)
    with job.lock():
        if args.url:
            if not args.video:
                # Cached file can be used without revisiting an expiring signed link.
                cached = job.dir / job.data['media'].get('path', '__absent__')
                if (job.data['source_url'] == clean_url(args.url) and cached.is_file()
                        and job.data['media'].get('sha256') == sha256(cached)):
                    video = cached
                else:
                    video = download(job, args.url)
            else:
                canonical = clean_url(args.url)
                if job.data['source_url'] not in (None, canonical):
                    raise ValueError('Different source URL requires a new job')
                job.data.update(source_url=canonical, input_kind='url_with_file')
                video = Path(args.video)
        elif args.video:
            video = Path(args.video)
            job.data['input_kind'] = 'file'
        elif job.data['media'].get('path'):
            video = job.dir / job.data['media']['path']
        else:
            video = None
        if args.metadata:
            import_metadata(job, args.metadata)
        if video is not None:
            try:
                video = probe(job, Path(video).resolve())
            except Exception as exc:
                job.stage('media', 'failed', '视频校验失败：' + type(exc).__name__)
                video = None
        if video is not None:
            if not args.skip_transcribe:
                try:
                    transcribe(job, video, args.model, args.language, args.offline)
                except Exception as exc:
                    job.stage('transcription', 'blocked', '转写未完成，保留进度：' + type(exc).__name__)
            else:
                if job.data['stages']['transcription']['status'] == 'pending':
                    job.stage('transcription', 'skipped', '本次明确跳过转写')
            try:
                frames(job, video, args.interval)
            except Exception as exc:
                job.stage('visual', 'failed', '抽帧未完成：' + type(exc).__name__)
            if args.review:
                apply_review(job, args.review)
        export(job)
    print('JOB=' + str(job.dir), flush=True)
    return 0 if job.data['status'] == 'completed' else 2


def doctor():
    details = dict(python=sys.executable, root=str(ROOT), runtime=str(RUNTIME),
                   temp=os.environ['TEMP'], model_cache=str(RUNTIME / 'models'),
                   ffmpeg=binary('ffmpeg'), ffprobe=binary('ffprobe'), packages={})
    probes = {'faster-whisper': ('faster_whisper', 'WhisperModel'),
              'yt-dlp': ('yt_dlp', 'YoutubeDL'), 'PyYAML': ('yaml', 'safe_load'),
              'Pillow': ('PIL.Image', 'open')}
    for name, (module_name, capability) in probes.items():
        try:
            module = importlib.import_module(module_name)
            if not callable(getattr(module, capability, None)):
                raise ImportError('Required package capability is unavailable')
            try:
                details['packages'][name] = (importlib.metadata.version(name) or
                                             getattr(module, '__version__', 'available; version metadata not readable'))
            except (importlib.metadata.PackageNotFoundError, PermissionError):
                details['packages'][name] = getattr(module, '__version__', 'available; version metadata not readable')
        except (ImportError, OSError):
            details['packages'][name] = None
    print(json.dumps(details, ensure_ascii=False, indent=2))
    return 0 if all(details['packages'].values()) else 1


def main():
    configure_storage()
    parser = argparse.ArgumentParser(description='D 盘本地视频文案与分镜准备工具')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    p = sub.add_parser('run')
    p.add_argument('--url')
    p.add_argument('--video')
    p.add_argument('--job', help='New or existing task directory on D:')
    p.add_argument('--model', default='small')
    p.add_argument('--language', default='zh')
    p.add_argument('--interval', type=float, default=15)
    p.add_argument('--skip-transcribe', action='store_true')
    p.add_argument('--offline', action='store_true', help='Do not download ASR models')
    p.add_argument('--metadata')
    p.add_argument('--review')
    for name in ('review', 'metadata'):
        s = sub.add_parser(name)
        s.add_argument('--job', required=True)
        s.add_argument('--file', required=True)
    s = sub.add_parser('refresh-metadata')
    s.add_argument('--job', required=True)
    s.add_argument('--url', required=True)
    e = sub.add_parser('export')
    e.add_argument('--job', required=True)
    args = parser.parse_args()
    if args.command == 'doctor':
        return doctor()
    if args.command == 'run':
        if not (args.url or args.video or args.job):
            parser.error('run requires --url, --video or an existing --job')
        return run(args)
    job = Job(args.job)
    with job.lock():
        if args.command == 'review':
            apply_review(job, args.file)
        elif args.command == 'metadata':
            import_metadata(job, args.file)
        elif args.command == 'refresh-metadata':
            if job.data['source_url'] != clean_url(args.url):
                raise ValueError('Metadata source mismatch')
            xhs_public_metadata(job, args.url)
        export(job)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('Interrupted; task progress is saved.', file=sys.stderr)
        sys.exit(130)
    except Exception as error:
        print('Operation failed: ' + type(error).__name__ + '. Check inputs or saved stage status.', file=sys.stderr)
        sys.exit(1)
