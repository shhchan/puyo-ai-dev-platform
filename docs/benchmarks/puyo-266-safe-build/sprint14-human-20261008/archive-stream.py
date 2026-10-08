"""Stream a large diagnostic replay, preserving input/hash and original SHA."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


class Reader:
    def __init__(self, source):
        self.source, self.buffer, self.end = source, '', False
        self.decoder = json.JSONDecoder()

    def fill(self, size=65536):
        value = self.source.read(size)
        self.buffer += value
        self.end = not value

    def peek(self):
        while True:
            self.buffer = self.buffer.lstrip()
            if self.buffer or self.end:
                return self.buffer[:1]
            self.fill()

    def take(self, char):
        if self.peek() != char:
            raise ValueError(f'expected {char!r}, got {self.buffer[:20]!r}')
        self.buffer = self.buffer[1:]

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer)
                if end == len(self.buffer) and not self.end:
                    self.fill()
                    continue
                self.buffer = self.buffer[end:]
                return value
            except json.JSONDecodeError:
                if self.end:
                    raise
                self.fill(max(65536, len(self.buffer)))


def archive_replay(source, output):
    digest = hashlib.sha256()
    with source.open('rb') as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    omitted = ('policy_diagnostics', 'controller_diagnostics', 'controller_status')
    count = 0
    with source.open() as handle, output.open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, filename='') as target:
        reader = Reader(handle)
        def emit(value):
            target.write(value.encode())
        reader.take('{')
        emit('{')
        first = True
        while reader.peek() != '}':
            if not first:
                reader.take(',')
                emit(',')
            first = False
            key = reader.value()
            reader.take(':')
            emit(json.dumps(key) + ':')
            if key != 'ticks':
                emit(json.dumps(reader.value(), separators=(',', ':')))
                continue
            reader.take('[')
            emit('[')
            while reader.peek() != ']':
                if count:
                    reader.take(',')
                    emit(',')
                row = reader.value()
                emit(json.dumps({k: v for k, v in row.items() if k not in omitted}, separators=(',', ':')))
                count += 1
            reader.take(']')
            emit(']')
        reader.take('}')
        emit('}')
        if reader.peek():
            raise ValueError('unexpected suffix')
    return {'bytes': source.stat().st_size, 'sha256': digest.hexdigest(), 'omitted_tick_keys': omitted, 'ticks': count}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    value = archive_replay(args.source, args.output)
    args.manifest.write_text(json.dumps(value, indent=2) + '\n')
    print(value)
