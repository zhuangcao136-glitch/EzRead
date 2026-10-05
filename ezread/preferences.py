"""Validate settings and saved reader state."""
from __future__ import annotations
from .context import ApplicationContext
import math
from datetime import datetime


def valid_year(value, minimum=1900):
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= datetime.now().year


def validate_settings(app: ApplicationContext, data):
    if not isinstance(data, dict):
        raise ValueError('设置必须是对象。')
    data = {k: v for k, v in data.items() if k in app.DEFAULT_SETTINGS}
    choices = {'theme': ('paper', 'cream', 'sage', 'graphite'), 'ui_font': ('system', 'sans', 'serif'),
               'reader_font': ('system', 'sans', 'serif')}
    for key, values in choices.items():
        if key in data and data[key] not in values:
            raise ValueError(key + ' 选项无效。')
    for key, low, high in (('ui_font_size', 14, 22), ('reader_font_size', 14, 28)):
        if key in data and (not isinstance(data[key], int) or isinstance(data[key], bool) or not low <= data[key] <= high):
            raise ValueError(f'{key} 必须是 {low}–{high} 范围内的整数。')
    if 'reader_sync' in data and not isinstance(data['reader_sync'], bool):
        raise ValueError('阅读同步状态必须为布尔值。')
    if 'reader_split_ratio' in data and not app.valid_ratio(data['reader_split_ratio'], .3, .7):
        raise ValueError('阅读分栏比例必须在 0.3–0.7 之间。')
    if {'translation_model', 'translation_reasoning_effort'} & data.keys():
        combined = {**app.settings(), **data}
        for key in ('translation_model', 'translation_reasoning_effort'):
            if not isinstance(combined[key], str) or len(combined[key]) > 160:
                raise ValueError('翻译模型与推理强度必须是有效文字选项。')
        if combined['translation_model'] or combined['translation_reasoning_effort']:
            import codex_models
            codex_models.resolve_config(combined['translation_model'], combined['translation_reasoning_effort'])
    if 'selection_translation_model' in data:
        if not isinstance(data['selection_translation_model'], str) or len(data['selection_translation_model']) > 160:
            raise ValueError('划线翻译模型无效。')
        if data['selection_translation_model']:
            import codex_models
            codex_models.resolve_config(data['selection_translation_model'])
    if 'sort' in data and (not isinstance(data['sort'], str) or len(data['sort']) > 40):
        raise ValueError('排序设置无效。')
    if 'collections' in data and (not isinstance(data['collections'], list) or len(data['collections']) > 1000 or any(not isinstance(x, str) or not x.strip() or len(x) > 100 for x in data['collections'])):
        raise ValueError('主题集合必须是非空名称列表。')
    return data


def valid_ratio(value, low=0, high=1):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high


def validate_reader_state(app: ApplicationContext, value, doc):
    if not isinstance(value, dict):
        raise ValueError('阅读位置必须是对象。')
    allowed = {'version', 'page', 'original_offset', 'translation_block_id', 'translation_offset', 'zoom', 'split_ratio', 'mode'}
    if set(value) - allowed:
        raise ValueError('阅读位置包含未知字段。')
    merged = {**doc.get('reader_state', {}), **value}
    merged.setdefault('version', 1)
    if merged['version'] != 1 or isinstance(merged['version'], bool):
        raise ValueError('阅读位置版本无效。')
    page = merged.get('page', doc.get('read_page', 1))
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= max(1, len(doc.get('pages', []))):
        raise ValueError('阅读页码超出范围。')
    merged['page'] = page
    for key in ('original_offset', 'translation_offset'):
        if key in merged and not app.valid_ratio(merged[key]):
            raise ValueError('阅读页内偏移必须在 0–1 之间。')
    block_id = merged.get('translation_block_id')
    if block_id is not None and (not isinstance(block_id, str) or not any(b['id'] == block_id for b in doc.get('blocks', []))):
        raise ValueError('译文定位段落不属于这篇论文。')
    if 'zoom' in merged and (isinstance(merged['zoom'], bool) or merged['zoom'] not in ('fit', 75, 100, 125, 150, 200)):
        raise ValueError('PDF 缩放选项无效。')
    if 'split_ratio' in merged and not app.valid_ratio(merged['split_ratio'], .3, .7):
        raise ValueError('阅读分栏比例必须在 0.3–0.7 之间。')
    if 'mode' in merged and merged['mode'] not in ('parallel', 'original', 'translation'):
        raise ValueError('阅读模式无效。')
    return merged
