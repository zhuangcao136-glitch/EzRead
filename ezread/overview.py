"""Paper-specific overview validation and text shared by history and handoff."""
from __future__ import annotations


def validate_overview(result):
    if not isinstance(result, dict):
        raise ValueError('研究速览格式不完整，请重试。')
    for key in ('title_zh', 'summary'):
        if not isinstance(result.get(key), str) or not result[key].strip():
            raise ValueError('研究速览格式不完整，请重试。')
    sections = result.get('overview_sections')
    if not isinstance(sections, list) or not sections:
        raise ValueError('研究速览缺少有效内容，请重试。')
    normalized = []
    for section in sections:
        if not isinstance(section, dict) or any(
            not isinstance(section.get(key), str) or not section[key].strip()
            for key in ('title', 'content')
        ):
            raise ValueError('研究速览分节格式不完整，请重试。')
        normalized.append({key: section[key].strip() for key in ('title', 'content')})
    if not isinstance(result.get('tags'), list):
        raise ValueError('研究速览关键词格式不完整，请重试。')
    return {'title_zh': result['title_zh'].strip(), 'summary': result['summary'].strip(),
            'overview_sections': normalized,
            'tags': [tag.strip() for tag in result['tags'] if isinstance(tag, str) and tag.strip()][:8]}


def overview_text(doc):
    parts = [doc.get('summary', '').strip()]
    sections = doc.get('overview_sections')
    if not isinstance(sections, list) or not sections:
        # Existing documents remain readable without rewriting or regenerating them.
        sections = [{'title': title, 'content': doc.get(key, '')} for key, title in (
            ('problem', '研究问题'), ('method', '方法与装置'),
            ('results', '主要结果'), ('limitations', '局限与边界'))]
    for section in sections:
        if not isinstance(section, dict):
            continue
        title, content = section.get('title'), section.get('content')
        if isinstance(title, str) and isinstance(content, str) and title.strip() and content.strip():
            parts.append(f'## {title.strip()}\n\n{content.strip()}')
    return '\n\n'.join(part for part in parts if part)
