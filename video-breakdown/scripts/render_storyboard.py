"""Render planned shots as clearly labelled storyboard PNGs and a Markdown script.

Requires Pillow. Uses no network, video generation or transcription.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

WIDTH, HEIGHT = 720, 1280
INK, MUTED, ACCENT = '#19372f', '#586c67', '#1c7d69'
PAPER, LINE, ORANGE = '#fffdf7', '#bfd3cc', '#d78b37'
LAYOUTS = {'talking_head', 'screen_demo', 'split', 'comparison', 'text_card'}


def choose_font(explicit: str | None) -> Path:
    candidates = [Path(explicit)] if explicit else [
        Path('C:/Windows/Fonts/msyh.ttc'),
        Path('C:/Windows/Fonts/simhei.ttf'),
        Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise ValueError('No CJK font found; provide --font pointing to an existing font.')


class Painter:
    def __init__(self, font_path: Path):
        self.font_path = str(font_path)
        self.fonts: dict[int, ImageFont.FreeTypeFont] = {}

    def font(self, size: int):
        if size not in self.fonts:
            self.fonts[size] = ImageFont.truetype(self.font_path, size)
        return self.fonts[size]

    def lines(self, draw, value, size, max_width):
        rows = []
        for paragraph in str(value).split('\n'):
            row = ''
            for char in paragraph:
                if row and draw.textlength(row + char, font=self.font(size)) > max_width:
                    rows.append(row)
                    row = ''
                row += char
            rows.append(row)
        return rows

    def text(self, draw, xy, value, size=27, color=INK, max_width=None):
        x, y = xy
        rows = self.lines(draw, value, size, max_width) if max_width else str(value).split('\n')
        for row in rows:
            draw.text((x, y), row, font=self.font(size), fill=color)
            y += int(size * 1.45)
        return y

    def fitted(self, draw, box, value, max_size=30, min_size=17, color=INK):
        x0, y0, x1, y1 = box
        for size in range(max_size, min_size - 1, -1):
            rows = self.lines(draw, value, size, x1 - x0)
            if len(rows) * int(size * 1.45) <= y1 - y0:
                return self.text(draw, (x0, y0), value, size, color, x1 - x0)
        raise ValueError(f'Text too long for storyboard area: {str(value)[:70]}')


def load_manifest(input_path: Path):
    data = json.loads(input_path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or not isinstance(data.get('shots'), list) or not data['shots']:
        raise ValueError('Manifest must have a non-empty shots list.')
    seen = set()
    for shot in data['shots']:
        if not isinstance(shot, dict):
            raise ValueError('Each shot must be an object.')
        shot_id = shot.get('id', '')
        if not isinstance(shot_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}', shot_id):
            raise ValueError('Shot id must be a safe unique filename token, up to 48 characters.')
        if shot_id in seen:
            raise ValueError(f'Duplicate shot id: {shot_id}')
        seen.add(shot_id)
        if shot.get('layout') not in LAYOUTS:
            raise ValueError(f'Unsupported layout in {shot_id}; choose from {sorted(LAYOUTS)}')
        for key in ('label', 'speech', 'on_screen', 'visual', 'motion', 'audio', 'asset_status', 'timing', 'screen_label', 'before', 'after'):
            value = shot.get(key, '')
            if not isinstance(value, str):
                raise ValueError(f'{shot_id}.{key} must be a string.')
        for key in ('label', 'speech', 'visual'):
            if not shot.get(key, '').strip():
                raise ValueError(f'{shot_id}.{key} is required.')
        asset = shot.get('asset')
        if asset is not None:
            if not isinstance(asset, str) or not asset:
                raise ValueError(f'{shot_id}.asset must be an image path or null.')
            resolved = Path(asset)
            if not resolved.is_absolute():
                resolved = input_path.parent / resolved
            if not resolved.is_file():
                raise ValueError(f'Image asset does not exist for {shot_id}: {resolved}')
            with Image.open(resolved) as probe:
                probe.verify()
            shot['_asset_path'] = resolved.resolve()
    return data


def panel(draw, box, fill='#eef5f1'):
    draw.rounded_rectangle(box, radius=18, fill=fill, outline=LINE, width=3)


def person(draw, box, p: Painter):
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    radius = min((x1 - x0) * .22, (y1 - y0) * .15)
    cy = y0 + (y1 - y0) * .28
    draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), outline=ACCENT, width=5, fill='#e4efe8')
    draw.arc((x0+12, cy+radius+15, x1-12, y1+70), 180, 360, fill=ACCENT, width=6)
    draw.line((cx-radius*.65, cy+radius*.25, cx+radius*.65, cy+radius*.25), fill=ACCENT, width=3)
    p.fitted(draw, (x0+15, y1-58, x1-15, y1-8), '本人出镜／待拍', 23)


def screen(im, draw, box, shot, p: Painter):
    panel(draw, box, '#f5f8f4')
    x0, y0, x1, y1 = map(int, box)
    caption = '实际素材参考' if shot.get('_asset_path') else '示意占位／待录屏'
    p.text(draw, (x0+15, y0+12), caption, 20, MUTED)
    content_box = (x0+18, y0+58, x1-18, y1-87)
    if shot.get('_asset_path'):
        with Image.open(shot['_asset_path']) as asset:
            thumb = ImageOps.contain(asset.convert('RGB'), (content_box[2]-content_box[0], content_box[3]-content_box[1]))
        im.paste(thumb, (content_box[0]+(content_box[2]-content_box[0]-thumb.width)//2,
                         content_box[1]+(content_box[3]-content_box[1]-thumb.height)//2))
    else:
        cx0, cy0, cx1, cy1 = content_box
        draw.rounded_rectangle(content_box, radius=8, outline=LINE, width=2, fill='white')
        p.fitted(draw, (cx0+15, cy0+18, cx1-15, min(cy1-10,cy0+115)), shot.get('screen_label') or '信息或成果区域', 28)
        for ratio, length in ((.43,.85),(.56,.7),(.69,.8),(.82,.6)):
            y=cy0+(cy1-cy0)*ratio
            draw.line((cx0+20,y,cx0+20+(cx1-cx0-40)*length,y),fill=LINE,width=9)
    p.fitted(draw, (x0+15,y1-69,x1-15,y1-12), shot.get('screen_label') or '按口播指向对应区域', 23)


def render_shot(shot, p: Painter):
    im = Image.new('RGB', (WIDTH, HEIGHT), PAPER)
    d = ImageDraw.Draw(im)
    p.fitted(d, (30,20,690,72), f"{shot['id']}  {shot['label']}", 31)
    p.text(d, (30,82), '画面草稿 · 非成片 · 9:16', 23, MUTED)
    p.fitted(d, (30,122,690,167), shot.get('timing') or '拟时间待试读', 23, color=MUTED)
    layout = shot['layout']
    if layout == 'talking_head':
        panel(d, (40,187,680,866))
        person(d, (125,250,595,835), p)
    elif layout == 'screen_demo':
        screen(im,d,(40,187,680,866),shot,p)
    elif layout == 'split':
        panel(d,(40,187,310,866))
        person(d,(55,290,295,835),p)
        screen(im,d,(330,187,680,866),shot,p)
    elif layout == 'comparison':
        for y,label,field in ((187,'版本A／事实状态见脚本','before'),(540,'版本B／事实状态见脚本','after')):
            panel(d,(40,y,680,y+326),'#f4f1e8' if field=='before' else '#eaf4ee')
            p.text(d,(60,y+18),label,23,MUTED)
            p.fitted(d,(64,y+82,656,y+296),shot.get(field) or '在清单中填写对应版本内容',34)
    else:
        panel(d,(40,187,680,866),'#eaf4ee')
        p.fitted(d,(74,260,646,800),shot.get('screen_label') or shot.get('on_screen') or shot['label'],46)
    panel(d,(40,889,680,1010),'#fff4df')
    p.fitted(d,(58,904,662,998),shot.get('on_screen') or '字幕区／随录音补齐',34)
    p.fitted(d,(40,1032,680,1114),'构图：'+shot['visual'],25)
    p.fitted(d,(40,1131,680,1199),'动作：'+(shot.get('motion') or '按口播安排'),24,color=MUTED)
    p.text(d,(40,1230),'制作示意；完整口播与素材状态见拍摄脚本。',20,MUTED)
    return im


def md_cell(value):
    return str(value).replace('|','\\|').replace('\r','').replace('\n','；')


def render(input_path: Path, output: Path, font_path: Path):
    data=load_manifest(input_path)
    p=Painter(font_path)
    # Render in memory before writing, so invalid text does not leave an incomplete new set.
    images=[render_shot(shot,p) for shot in data['shots']]
    output.mkdir(parents=True,exist_ok=True)
    entries=[]
    for shot,im in zip(data['shots'],images):
        filename=f"shot-{shot['id']}.png"
        im.save(output/filename)
        entries.append({**{k:v for k,v in shot.items() if not k.startswith('_')},'draft_image':filename})
    contacts=[]
    for start in range(0,len(images),6):
        group=images[start:start+6]
        cols=min(3,len(group));rows=(len(group)+cols-1)//cols
        cw,ch,gap=360,640,20
        contact=Image.new('RGB',(cols*cw+(cols+1)*gap,rows*ch+(rows+1)*gap+65),'#e3ebe6')
        cd=ImageDraw.Draw(contact)
        p.text(cd,(20,15),f"画面草稿总览 {start//6+1} · 非成片",26)
        for n,im in enumerate(group):
            contact.paste(im.resize((cw,ch),Image.Resampling.LANCZOS),
                          (gap+(n%cols)*(cw+gap),65+gap+(n//cols)*(ch+gap)))
        filename=f'storyboard-contact-{start//6+1:02}.png'
        contact.save(output/filename);contacts.append(filename)
    index={'title':data.get('title') or '原创视频拍摄脚本','input':str(input_path.resolve()),
           'status':'draft_unreviewed','canvas':{'width':WIDTH,'height':HEIGHT},'shots':entries,'contact_sheets':contacts}
    (output/'storyboard-index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    md=['# '+index['title'],'','**画面草稿 · 非成片。** 时间为拟定预算，等待试读后调整；画面与声音均为设计安排。','',
        '## 拍摄脚本','', '| 镜头 | 拟时间 | 口播 | 画面与动作 | 屏幕文字 | 声音 | 素材状态 |','|---|---|---|---|---|---|---|']
    for shot in entries:
        cells=[shot['id']+' '+shot['label'],shot.get('timing') or '待试读',shot['speech'],
               shot['visual']+'；'+shot.get('motion',''),shot.get('on_screen',''),shot.get('audio','待录'),shot.get('asset_status','待确认')]
        md.append('| '+' | '.join(md_cell(c) for c in cells)+' |')
    md+=['','## 草稿总览','']
    for filename in contacts:md += [f'![画面草稿总览]({filename})','']
    md+=['## 逐镜头草稿','']
    for shot in entries:
        md += [f"### {shot['id']} {shot['label']}",'',f"![{shot['id']}画面草稿]({shot['draft_image']})",'',
               '**口播：**'+shot['speech'],'','**素材状态：**'+(shot.get('asset_status') or '待确认'),'']
    (output/'拍摄脚本_含画面草稿.md').write_text('\n'.join(md),encoding='utf-8')
    return {'shots':len(entries),'contact_sheets':len(contacts),'script':str(output/'拍摄脚本_含画面草稿.md')}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--font')
    args=parser.parse_args()
    try:
        result=render(args.input,args.output,choose_font(args.font))
    except (ValueError,OSError,json.JSONDecodeError) as exc:
        parser.exit(2,f'Error: {exc}\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
