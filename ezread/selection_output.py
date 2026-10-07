"""Readable public CLI diagnostics and incremental schema output.

Never expose credentials, request payloads or private reasoning in a progress UI.
Known runtime messages are translated locally; unfamiliar public details survive
redaction so a failure does not become an uninformative generic status.
"""
import json
import re

_PHRASES = (
    (r'failed to refresh available models', '刷新可用模型失败'),
    (r'disable_response_storage is ignored', '已忽略禁用响应存储的配置'),
    (r'network is not available', '网络不可用'),
    (r'network (?:is )?unavailable', '网络不可用'),
    (r'stream disconnected before completion', '响应在完成前断开'),
    (r'stream disconnected', '响应连接已断开'),
    (r'reconnecting', '正在重新连接'),
    (r'retrying', '正在重试'),
    (r'retry attempt', '重试次数'),
    (r'will retry', '将重试'),
    (r'retry in', '将在以下时间后重试'),
    (r'error sending request', '发送请求失败'),
    (r'failed to connect', '连接失败'),
    (r'connection (?:reset|closed)', '连接已断开'),
    (r'connection refused', '连接被拒绝'),
    (r'connection error', '连接错误'),
    (r'no route to host', '无法连接到服务器'),
    (r'(?:dns error|failed to (?:resolve|lookup) (?:host|address))', '服务器地址解析失败'),
    (r'certificate verify failed|invalid peer certificate', '服务器证书验证失败'),
    (r'network error', '网络错误'),
    (r'error while reading response', '读取响应失败'),
    (r'(?:request )?timed out|request timeout', '请求超时'),
    (r'error sending request for url', '发送请求失败，服务地址'),
    (r'error decoding response body', '响应内容解析失败'),
    (r'error (?:during|in) websocket', 'WebSocket 连接错误'),
    (r'falling back to', '正在切换至'),
    (r'websocket', 'WebSocket'),
    (r'http status|status code', 'HTTP 状态码'),
    (r'unexpected status', '异常状态'),
    (r'service unavailable', '服务暂不可用'),
    (r'rate limit(?: exceeded)?', '请求频率受限'),
    (r'usage limit(?: reached)?', '订阅用量受限'),
    (r'unauthorized|authentication failed', '登录认证失败'),
    (r'access is denied|permission denied', '没有访问权限'),
    (r'for url', '服务地址'),
)


def diagnostic(raw):
    """Keep user-facing failures/retries, strip log framing and sensitive fields."""
    if not isinstance(raw, str):
        return ''
    text = re.sub(r'\x1b\[[0-9;]*m', '', raw).strip()
    if not text:
        return ''
    # These are internal data, never public model output or runtime diagnostics.
    if re.search(r'UNTRUSTED_PAPER_DATA|(?:reasoning(?:_\w+)?|analysis|input(?:_\w+)?|prompt|base_instructions|encrypted_content|raw_content)"?\s*[=:]|"type"\s*:\s*"reasoning"', text, re.I):
        return ''
    text = re.sub(r'\b(?:Bearer\s+\S+|sk-[\w-]+|eyJ[\w.-]{20,})', '[凭据已隐藏]', text, flags=re.I)
    text = re.sub(r'(?i)(authorization|(?:access|refresh|id)_token|api[_ -]?key|cookie)\s*[=:]\s*(?:"[^"]*"|\S+)',
                  r'\1=[凭据已隐藏]', text)
    text = re.sub(r'https?://[^\s)\]"<>]+', '[服务地址]', text)
    text = re.sub(r'[A-Za-z]:[\\/][^\s"<>]+', '[本机路径]', text)
    text = re.sub(r'^\d{4}-\d\d-\d\d[T ][\d:.+Z-]+\s*', '', text)
    text = re.sub(r'^(?:TRACE|DEBUG|INFO|WARN|ERROR)\s+[\w:]+:\s*', '', text)
    for pattern, translated in _PHRASES:
        text = re.sub(pattern, translated, text, flags=re.I)
    return text[:2400]


def partial_translation(raw):
    """Expose only the translation string from partial JSON, never JSON framing."""
    match = re.match(r'\s*\{\s*"translation"\s*:\s*"', raw)
    if not match:
        return ''
    start = match.end() - 1
    end = start + 1
    while end < len(raw):
        if raw[end] == '"':
            try:
                return json.loads(raw[start:end + 1]).encode('utf-8', errors='ignore').decode('utf-8')
            except ValueError:
                return ''
        if raw[end] == '\\':
            width = 6 if raw[end:end + 2] == '\\u' else 2
            if end + width > len(raw):
                break
            end += width
        else:
            end += 1
    try:
        return json.loads(raw[start:end] + '"').encode('utf-8', errors='ignore').decode('utf-8')
    except ValueError:
        return ''
