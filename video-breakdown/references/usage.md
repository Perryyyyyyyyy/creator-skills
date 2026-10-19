# 本地执行说明

当前技能包含“拆解文档→本人内容→原创文案与含画面草稿的脚本→核对”流程。下述既有CLI继续负责媒体、转写、抽帧和原视频报告；原创写作及核对由当前助手按 [creation-workflow.md](creation-workflow.md) 执行，不把 `run` 预处理完成称为全流程完成。

运行位置为 `D:/codexfile/mySkill/video-breakdown`。便携 Python、Python 依赖、模型、临时文件和下载缓存都在 `.runtime` 内；任务素材与结果在 `runs` 内。不修改全局 PATH。复用已有的 `D:/Apps/FFmpeg/bin`。

## 已接通的功能

HTTPS 视频链接获取使用 yt-dlp；小红书公开页面能读取时补充作者、发布时间及本条互动数，并尝试读取作者公开主页的粉丝显示值与简介。区间值原样保留，不换算为精确粉丝数。是否需要登录、链接是否有效仍以实际请求为准。评论全量采集未实现，可由助手通过可用浏览器补充样本并注明来源。没有平台绕过或签名生成器。

本地视频通过 FFprobe 和解码校验后，使用 FFmpeg 提取 PCM 音频，faster-whisper 在 CPU 上转写；模型在 D 盘下载一次，之后可以离线使用。默认 small 中文模型，输出是机器初稿。

工具自动抽取带时间码的关键帧和联系表。随后由当前助手读取关键帧、文案并导入分析，不需要另行配置收费的语言模型 API。脚本自己不进行语义画面理解；命令行预处理结束后的 partial 是正常待分析状态。

新增 `scripts/render_storyboard.py` 从助手已写好的镜头清单绘制逐镜头草图、分页总览和脚本Markdown，复用现有Pillow与本机中文字体。它不调用视频下载、转写或生成式视频，不需要重新配置API。输入格式和打包方式见 [creation-workflow.md](creation-workflow.md)。

## 执行命令

以下 PowerShell 命令中的相对路径以技能目录为当前目录。也可使用 `run.ps1` 的完整路径。

```powershell
# 检查路径和运行依赖
.\run.ps1 doctor

# 获取链接并进行本地处理，首次模型下载需要网络
.\run.ps1 run --url '完整分享链接'

# 本地视频
.\run.ps1 run --video 'D:/素材/视频.mp4'

# 继续已有任务，并补入媒体；保留已采集的网页信息
.\run.ps1 run --job 'D:/codexfile/mySkill/video-breakdown/runs/任务目录' --video 'D:/素材/视频.mp4' --offline

# 模型准备好后离线继续转写
.\run.ps1 run --job 'D:/codexfile/mySkill/video-breakdown/runs/任务目录' --offline

# 导入助手写好的画面观察与八角度分析
.\run.ps1 review --job 'D:/codexfile/mySkill/video-breakdown/runs/任务目录' --file 'D:/素材/review.json'

# 导入额外公开页面数据或截图转录
.\run.ps1 metadata --job 'D:/codexfile/mySkill/video-breakdown/runs/任务目录' --file 'D:/素材/metadata.json'

# 仅重新采集笔记和账号公开数据，不重新转写视频
.\run.ps1 refresh-metadata --job 'D:/codexfile/mySkill/video-breakdown/runs/任务目录' --url '完整分享链接'
```

退出码 0 表示所运行的命令成功；run 的退出码 2 表示产物已保存，但完整拆解还有缺失模块；1 表示命令失败。PowerShell 的外层宿主可能把非零退出码统一显示为失败，应同时看 record.json 和已生成文件。

默认每 15 秒采样，并补充 0、2、5、10 秒和结尾，最多 600 帧。用 `--interval` 调整。不要将采样间隔视为真实镜头长度。原视频必须可解码；不接受只有封面或无视频的音频文件作为完整视频分析。

## 助手完成分析的交接

先读取 `record.json`、`transcript/raw.json` 和 `frames/index.json`，查看全部联系表。对开头、白板小字、转折与结尾再查看独立原尺寸帧；需要更密集的观察时调整采样或补帧。

编写 review.json，字段见下例。media_sha256 必须与任务媒体一致，frame_ids 与 transcript_ids 必须是现有证据。reviewed_frame_ids 只填写实际看过的帧。analysis 的八个 dimension 名称以脚本中的 DIMENSIONS 为准。

```json
{
  "media_sha256": "实际媒体指纹",
  "reviewed_frame_ids": ["f0001"],
  "visual_segments": [{
    "id": "s1",
    "start_seconds": 0,
    "end_seconds": 5,
    "frame_ids": ["f0001"],
    "transcript_ids": ["t0001"],
    "shot_description": "根据实际画面填写",
    "actions": "只描述有证据的动作",
    "screen_text": "实际可辨识文字",
    "audio_observations": "仅填写实际听辨的声音特征",
    "editing_observations": "实际观察到的剪辑",
    "narrative_role": "该段在结构中的作用",
    "boundary_precision": "sampled_interval"
  }],
  "analysis": [{
    "dimension": "目标人群",
    "finding": "有证据支持的发现",
    "claim_type": "inference",
    "evidence_ids": ["f0001", "t0001"],
    "limitation": "结论的适用范围"
  }]
}
```

元数据导入格式：source_url 为对应笔记地址，observed_at 使用带时区的时间，method 说明读取方式。fields 的每项含 value、raw_display、status 和 note；status 取 observed、approximate、inferred 或 unavailable。只导入确实读到的信息；旧数据保留原采集日期。

导入之后报告自动更新。分镜表与逐字稿等只在确有相应记录时生成。SRT 和逐字稿来自机器初稿，不代表已逐句听校。声音、表情变化及镜头转换等没有证据时留空。

这些命令的基础导出属于处理稿，不是图文版的最终排版。媒体与分析完成后，继续按 [报告整理与导出规范](report-delivery.md) 整理正文、合并适用的完整逐字稿，并按 [图表规范](charts.md) 为八个角度制作图表，再调用 scripts/package_report.py 生成图文报告。

历史任务 runs 内的图表脚本包含该条视频的具体内容，仅用于查看曾有的实现。不要直接复制运行到新任务或沿用其数值、节点、账号和情绪波形。新图必须依据新任务的原始记录与实际观察。

同一媒体和转写参数下，转写中断会保留已完成片段，恢复时从最后保存的结束时间继续；恢复边界记录在 record.json，必要时在该处复听。更换模型或语言则重新转写，避免混合不同处理结果。

## 安装和复现

本机运行时已部署。`scripts/setup.ps1` 用于重建便携环境，所有下载和安装仍在 D 盘；安装依赖版本由 requirements.lock.txt 固定。若 FFmpeg 不在指定 D 盘目录，需先在 D 盘提供 FFmpeg 与 FFprobe。

安装脚本不默认下载语音模型；首次执行 run 时下载到 `.runtime/models`。已有模型使用 `--offline` 避免网络请求。不要把 `.runtime` 与 `runs` 提交到版本库。

在本次受限环境中，普通执行和联网执行的包访问权限不同，因此保留了两个 D 盘依赖目录；正常本机运行使用 packages-host，packages 为受限执行备用，不修改系统权限。
