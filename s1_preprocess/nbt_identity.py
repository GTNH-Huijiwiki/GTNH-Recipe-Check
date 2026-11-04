"""比较 SNBT 复合结构，不依赖字段顺序，并保留标量类型与列表顺序。"""
import re

_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}\[\],:;]|[^\s{}\[\],:;]+')


def nbt_identity(text):
    tokens = _TOKEN.findall(text)
    cursor = 0

    def take(expected=None):
        nonlocal cursor
        token = tokens[cursor]
        cursor += 1
        if expected is not None and token != expected:
            raise ValueError('SNBT 标记顺序异常')
        return token

    def value():
        token = take()
        if token == '{':
            fields = []
            while tokens[cursor] != '}':
                key = take()
                take(':')
                # Minecraft 1.7 会以无引号形式写出带命名空间的复合键。
                # 值之前的最后一个冒号用于分隔键和值。
                while cursor + 1 < len(tokens) and tokens[cursor + 1] == ':':
                    key += ':' + take()
                    take(':')
                fields.append((key, value()))
                if tokens[cursor] != ',':
                    break
                take(',')
            take('}')
            return ('compound', tuple(sorted(fields)))
        if token == '[':
            entries = []
            while tokens[cursor] != ']':
                # 保留类型化数组前缀和旧版索引列表标签。
                if cursor + 1 < len(tokens) and tokens[cursor + 1] in (':', ';'):
                    entries.append(('prefix', take(), take()))
                entries.append(value())
                if tokens[cursor] != ',':
                    break
                take(',')
            take(']')
            return ('list', tuple(entries))
        if token in ('}', ']', ',', ':', ';'):
            raise ValueError('SNBT 值非法')
        return ('scalar', token)

    try:
        result = value()
        return result if cursor == len(tokens) else ('raw', text)
    except (ValueError, IndexError):
        return ('raw', text)
