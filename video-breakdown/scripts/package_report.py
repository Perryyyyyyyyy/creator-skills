"""Package an authored Markdown report as self-contained illustrated HTML. No network."""
from __future__ import annotations

import argparse
import base64
from html import escape
from pathlib import Path
import re
from urllib.parse import urlsplit, unquote

STYLE = '''body{margin:0;background:#f1f4f6;color:#233747;font:16px/1.85 "Microsoft YaHei","PingFang SC",sans-serif}main{max-width:980px;margin:32px auto;padding:48px 56px;background:white}h1{font-size:30px;line-height:1.5}h2{font-size:24px;margin-top:46px;border-bottom:1px solid #dce5eb;padding-bottom:10px}h3{font-size:20px;margin-top:32px;color:#176d85}p{margin:12px 0}li{margin:9px 0}a{color:#166b85}img{display:block;width:100%;height:auto;margin:22px 0 28px}table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:11px 13px;border:1px solid #dce5eb;text-align:left;vertical-align:top}th{background:#edf5f7}tr:nth-child(even){background:#f8fafb}.table-wrap{overflow-x:auto;margin:20px 0}pre{padding:16px;background:#f4f6f8;white-space:pre-wrap;overflow-wrap:anywhere}@media(max-width:700px){main{margin:0;padding:24px 18px}h1{font-size:25px}table{min-width:610px}}@media print{body{background:white}main{margin:0;max-width:none;padding:0}h2,h3{break-after:avoid}img,tr{break-inside:avoid}.table-wrap{overflow:visible}table{min-width:0!important;font-size:10px}}'''


def clean_markdown(source, strip_code_blocks=False):
    text = re.sub(r'<!--.*?-->', '', source, flags=re.S)
    text = re.sub(r'^\s*(?:证据|内部证据|source segments)\s*[:：]\s*(?:(?:[tf]\d+|page[-\w]+)[,\s，]*)+\s*$', '', text, flags=re.M)
    # Keep substantive tutorial code unless the caller explicitly requests removal.
    result, fence, keep = [], None, True
    for line in text.splitlines():
        match = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if match and fence is None:
            fence = (match[1][0], len(match[1]))
            keep = not (strip_code_blocks or match[2].strip().startswith(('internal', 'debug-internal')))
            if keep:
                result.append(line)
        elif match and fence and match[1][0] == fence[0] and len(match[1]) >= fence[1] and not match[2].strip():
            if keep:
                result.append(line)
            fence, keep = None, True
        elif keep:
            result.append(line)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(result)).strip() + '\n'


def safe_link(target, has_transcript):
    parsed = urlsplit(target)
    if parsed.scheme and parsed.scheme.lower() != 'https':
        return '#'
    if parsed.netloc and not parsed.scheme:
        return '#'
    if has_transcript and not parsed.scheme and '逐字稿' in unquote(parsed.path):
        return '#full-transcript'
    if not parsed.scheme and (re.match(r'^[a-zA-Z]:', target) or target.startswith(('\\', '/'))):
        return '#'
    return target


def render(source, source_dir, asset_roots):
    roots = [Path(source_dir).resolve()] + [Path(x).resolve() for x in asset_roots]
    embedded = []
    has_transcript = bool(re.search(r'^#{1,3}\s+.*完整(?:逐字|转写)稿', source, re.M))

    def image_tag(label, target):
        if target.startswith(('https:', 'http:', 'data:', 'file:')):
            raise ValueError('Use a local PNG/JPEG chart; remote image loading is not supported.')
        path = Path(unquote(target))
        path = (source_dir / path).resolve() if not path.is_absolute() else path.resolve()
        if not any(path.is_relative_to(root) for root in roots):
            raise ValueError('Chart is outside the explicitly allowed asset directories.')
        data = path.read_bytes()
        if len(data) > 20 * 1024 * 1024:
            raise ValueError('Chart exceeds the 20 MB per-image limit.')
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            kind = 'image/png'
        elif data.startswith(b'\xff\xd8\xff'):
            kind = 'image/jpeg'
        else:
            raise ValueError('Chart is not a PNG or JPEG image.')
        embedded.append(path)
        return '<img alt="' + escape(label, quote=True) + '" src="data:' + kind + ';base64,' + base64.b64encode(data).decode('ascii') + '">'

    def inline(value):
        # Isolate generated image tags before escaping all untrusted text.
        slots = []
        def capture(match):
            slots.append(image_tag(match[1], match[2]))
            return f'\x00IMAGE{len(slots)-1}\x00'
        value = re.sub(r'!\[([^]]*)\]\(([^)]+)\)', capture, value)
        value = escape(value, quote=True)
        def link(match):
            target = safe_link(match[2].replace('&amp;', '&'), has_transcript)
            return '<a href="' + escape(target, quote=True) + '">' + match[1] + '</a>'
        value = re.sub(r'\[([^]]+)\]\(([^)]+)\)', link, value)
        value = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', value)
        for i, tag in enumerate(slots):
            value = value.replace(f'\x00IMAGE{i}\x00', tag)
        return value

    def cells(row):
        return [inline(x.strip().replace('\\|', '|')) for x in re.split(r'(?<!\\)\|', row.strip().strip('|'))]

    parts, lines, pos, transcript_id_used = [], source.splitlines(), 0, False
    while pos < len(lines):
        line = lines[pos].strip()
        if not line:
            pos += 1
            continue
        fence = re.match(r'^(`{3,}|~{3,})(.*)$', line)
        if fence:
            marker, code = fence[1], []
            pos += 1
            while pos < len(lines):
                if re.fullmatch(re.escape(marker[0]) + '{' + str(len(marker)) + ',}', lines[pos].strip()):
                    pos += 1
                    break
                code.append(lines[pos])
                pos += 1
            parts.append('<pre><code>' + escape('\n'.join(code)) + '</code></pre>')
            continue
        if line.startswith('|') and pos+1 < len(lines) and re.fullmatch(r'[| :\-]+', lines[pos+1].strip()):
            rows = []
            while pos < len(lines) and lines[pos].strip().startswith('|'):
                rows.append(lines[pos].strip())
                pos += 1
            parts.append('<div class="table-wrap"><table><thead><tr>' + ''.join('<th>'+x+'</th>' for x in cells(rows[0])) + '</tr></thead><tbody>')
            for row in rows[2:]:
                parts.append('<tr>' + ''.join('<td>'+x+'</td>' for x in cells(row)) + '</tr>')
            parts.append('</tbody></table></div>')
            continue
        heading = re.match(r'^(#{1,3}) (.*)$', line)
        if heading:
            size, label = len(heading[1]), heading[2]
            identity = ''
            if not transcript_id_used and re.search(r'完整(?:逐字|转写)稿', label):
                identity, transcript_id_used = ' id="full-transcript"', True
            parts.append(f'<h{size}{identity}>' + inline(label) + f'</h{size}>')
            pos += 1
            continue
        if line.startswith('- ') or re.match(r'^\d+\. ', line):
            ordered = bool(re.match(r'^\d+\. ', line))
            tag, pattern = ('ol', r'^\d+\. ') if ordered else ('ul', r'^- ')
            parts.append('<'+tag+'>')
            while pos < len(lines) and re.match(pattern, lines[pos].strip()):
                value = re.sub(pattern, '', lines[pos].strip())
                if value.startswith('[ ] '):
                    value = '□ ' + value[4:]
                parts.append('<li>' + inline(value) + '</li>')
                pos += 1
            parts.append('</'+tag+'>')
            continue
        parts.append('<p>' + inline(line) + '</p>')
        pos += 1
    title = next((x[2:].strip() for x in lines if x.startswith('# ')), '视频拆解报告')
    document = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(title) + '</title><style>' + STYLE + '</style></head><body><main>' + ''.join(parts) + '</main></body></html>'
    return document, embedded


def package(source, output, asset_roots=(), strip_code_blocks=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.suffix.lower() != '.html':
        raise ValueError('Output must have the .html extension.')
    if output.drive and output.drive.upper() != 'D:':
        raise ValueError('The current storage preference requires output on D:.')
    clean_path = output.with_suffix('.md')
    if source == clean_path:
        raise ValueError('Choose a different output name to preserve the authored source.')
    cleaned = clean_markdown(source.read_text(encoding='utf-8-sig'), strip_code_blocks)
    document, embedded = render(cleaned, source.parent, asset_roots)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Markdown keeps valid absolute references; HTML has no local image dependencies.
    normalized = re.sub(r'!\[([^]]*)\]\(([^)]+)\)', lambda m: '!['+m[1]+']('+str((source.parent/Path(unquote(m[2]))).resolve()).replace('\\','/')+')', cleaned)
    clean_path.write_text(normalized, encoding='utf-8')
    output.write_text(document, encoding='utf-8')
    return {'html': str(output), 'markdown': str(clean_path), 'embedded_images': len(embedded)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Clean and package an authored report without remote image requests.')
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--asset-root', action='append', default=[])
    parser.add_argument('--strip-code-blocks', action='store_true', help='Explicitly remove all fenced code, including substantive code.')
    args = parser.parse_args()
    result = package(args.source, args.output, args.asset_root, args.strip_code_blocks)
    print('HTML=' + result['html'])
    print('MARKDOWN=' + result['markdown'])
    print('EMBEDDED_IMAGES=' + str(result['embedded_images']))
