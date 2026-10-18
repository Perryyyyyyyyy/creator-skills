# 报告与证据记录规范

本规范用于未来执行器和技能输出之间的数据交接。数据记录不要求固定工具；时间、来源和处理范围必须足以支持复核。

## 任务状态

record.json 至少包含 schema_version、task_id、created_at、updated_at、input_kind、source_url、media、stages、metadata、evidence、transcript_segments、visual_segments、analysis 和 missing_items。

整体状态取 completed、partial、blocked 或 failed。模块状态取 pending、running、completed、blocked、failed 或 skipped。状态 completed 表示约定处理已完成，不等于内容识别无误。字幕错误和未确定内容仍应单独标记。

| 模块 | 完成判据 |
|---|---|
| metadata | 已实际读取预定来源；每个字段有已观察或缺失的状态 |
| media | 实际文件存在、可解码，记录指纹、时长和音轨情况 |
| transcription | 有声范围全长已处理，并记录问题与未覆盖区间 |
| visual | 已执行约定抽样和重点核验，保留观察时间索引与粒度 |
| alignment | 文字与画面已关联；没有依据的细节不补写 |
| export | 所列交付文件实际生成，格式检查通过 |

因缺少可选账号指标而标为缺失，不阻止媒体模块完成。整体报告若仍缺用户要求的主要模块，则为 partial；不得标为完整拆解。

## 元数据字段

建议字段：platform、note_id、title、body、cover_text、author_name、author_profile_url、bio、verification、account_category、followers、published_at、duration_seconds、aspect_ratio、likes、saves、comments、shares、views。

每项字段保存：

- value：归一化值，未取得时为 null。
- raw_display：页面实际显示形式。
- status：observed、approximate、inferred 或 unavailable。
- observed_at：带时区的 ISO 8601 时间。
- evidence_ids：来源证据编号。
- note：精度、口径、推断依据或缺失原因。

published_at 另外保存 precision，例如 month_day、date 或 datetime。account_category 标明 platform_label 或 inferred_category。粉丝量等数据更新时新增快照，不覆盖旧记录。

## 媒体与证据

media 记录相对任务目录的文件路径、SHA-256、媒体时长、音轨是否存在、验证结果，以及工具实际返回的编码等信息。

证据类型包括 page_snapshot、frame、audio_segment、transcript_segment、user_text 和 author_claim。每条有唯一 ID、来源、采集时间及适用的媒体区间。截图路径与源页地址不能混为同一字段。

page_snapshot 保存可用页面地址和原始显示；frame 保存文件路径、time_seconds 与观察文字；audio_segment 保存音频路径与起止时间。分析通过 evidence_ids 引用这些证据。

仅作者公开声称使用某工具，标为作者自述；直接看到白板，标为画面观察。两者可互相支持，但不能互相替代。

## 转写片段

transcript_segments 每段记录 id、start_seconds、end_seconds、raw_text、edited_text、speaker_label、review_status、corrections 和 evidence_ids。说话人无法确定时为空；没有工具或人工依据时不生成置信度。

时间约束为 0 <= start_seconds < end_seconds <= duration_seconds。保留分块偏移表及合并记录；允许真实多人重叠片段，但 SRT 导出需按选择的字幕表现方式处理并记录调整。

同时记录 processed_audio_intervals、known_silence_intervals 和 unresolved_intervals。不能用“最后一个转写片段接近结尾”替代全音频处理范围验证。静音不需要制造字幕，也不自动等于漏转。

## 视觉片段与成片还原

visual_segments 每段记录 id、start_seconds、end_seconds、frame_ids、transcript_ids、shot_description、actions、screen_text、audio_observations、editing_observations、narrative_role 和 boundary_precision。

boundary_precision 区分 verified_transition、sampled_interval 和 unknown。段落表可以覆盖完整叙事时间轴，同时每段说明观察证据的稠密程度；不声称抽样间隔中的每个动作都已观察。

分镜 CSV 列为：起始秒、结束秒、起始时间码、结束时间码、粒度、画面、动作、口播、屏幕文字、声音、剪辑、段落作用、证据编号。输出给表格软件时，对以等号、加号、减号或 @ 开始的外部文本做安全文本处理，避免当作公式执行。

## 分析记录

analysis 每项记录 dimension、finding、claim_type、evidence_ids、scope 和 limitation。claim_type 为 observation、author_claim、inference 或 recommendation。

评论样本记录 sample_count、sampling_method、observed_at、是否包含作者回复；计数依据实际读取的评论，不用页面总评论数代替样本量。没有全量读取就不计算代表全部观众的比例。

互动公式保留分子和分母名称。没有播放量则不输出播放互动率；互动计数和不称为人数。与同账号其他视频比较时，记录样本链接、发布时间、采集时间和比较限制。

## 缺失与恢复

missing_items 每项包括 field_or_stage、reason、impact 和 next_input。原因具体区分 not_visible、access_required、media_unavailable、tool_unavailable、unreadable、not_processed 等，不把 unknown 写成确定故障。

缓存键包含媒体指纹、处理参数和执行器版本。只要输入或关键参数改变，依赖结果标记 stale 并重建；已保存的来源快照不删除。任务日志记录阶段和错误摘要，不包含 Cookie、密钥、签名地址或完整访问令牌。

## 交付摘要

报告开头展示实际完成状态、素材来源、采集日期和主要结论。报告末尾列明资料未覆盖的范围以及是否需要用户补充。把缺失影响集中说明，不在每段机械重复。

完整文案、段落还原、精确分镜和爆款原因验证是不同完成目标，逐项表述。最终只链接实际产物，不把拟生成的文件列为下载项。
