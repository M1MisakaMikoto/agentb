from __future__ import annotations

import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def read_ole_stream(data: bytes, stream_name: str) -> Optional[bytes]:
    """最小 OLE/CFB 读取器：按流名提取内容（仅标准库）。"""
    if len(data) < 512 or data[:8] != _OLE_MAGIC:
        return None

    sector_size = 1 << struct.unpack_from("<H", data, 30)[0]
    mini_sector_size = 1 << struct.unpack_from("<H", data, 32)[0]
    num_fat_sectors = struct.unpack_from("<I", data, 44)[0]
    root_start = struct.unpack_from("<I", data, 48)[0]
    mini_stream_cutoff = struct.unpack_from("<I", data, 56)[0]
    mini_fat_start = struct.unpack_from("<I", data, 60)[0]
    difat_start = struct.unpack_from("<I", data, 68)[0]
    num_difat_sectors = struct.unpack_from("<I", data, 72)[0]

    # DIFAT 头部 109 项：FAT 扇区编号列表
    fat_secs: List[int] = []
    for i in range(109):
        v = struct.unpack_from("<I", data, 76 + i * 4)[0]
        if v == 0xFFFFFFFF:
            break
        fat_secs.append(v)
    # 扩展 DIFAT 链：后续 FAT 扇区编号存于 DIFAT 扇区
    difat_sec = difat_start
    while num_difat_sectors > 1:
        off = 512 + difat_sec * sector_size
        for j in range(sector_size // 4 - 1):
            v = struct.unpack_from("<I", data, off + j * 4)[0]
            if v == 0xFFFFFFFF:
                break
            fat_secs.append(v)
        difat_sec = struct.unpack_from("<I", data, off + sector_size - 4)[0]
        num_difat_sectors -= 1

    # 逐扇区读取 FAT 条目，拼出完整 fat 表
    fat: List[int] = []
    for fs in fat_secs:
        off = 512 + fs * sector_size
        for j in range(sector_size // 4):
            fat.append(struct.unpack_from("<I", data, off + j * 4)[0])

    def read_chain(start: int) -> bytes:
        if start == 0xFFFFFFFE:
            return b""
        chunks: List[bytes] = []
        seen = set()
        sec = start
        while sec != 0xFFFFFFFE and sec not in seen and sec < 0xFFFFFFF0:
            seen.add(sec)
            off = 512 + sec * sector_size
            chunks.append(data[off : off + sector_size])
            if sec >= len(fat):
                break
            sec = fat[sec]
        return b"".join(chunks)

    def read_mini_chain(start: int, root_bytes: bytes) -> bytes:
        if start == 0xFFFFFFFE:
            return b""
        mini_fat: List[int] = []
        sec = mini_fat_start
        seen_fat = set()
        while sec != 0xFFFFFFFE and sec not in seen_fat and sec < 0xFFFFFFF0:
            seen_fat.add(sec)
            off = 512 + sec * sector_size
            for j in range(sector_size // 4):
                mini_fat.append(struct.unpack_from("<I", data, off + j * 4)[0])
            if sec >= len(fat):
                break
            sec = fat[sec]

        chunks: List[bytes] = []
        seen = set()
        s = start
        while s != 0xFFFFFFFE and s not in seen:
            seen.add(s)
            off = s * mini_sector_size
            chunks.append(root_bytes[off : off + mini_sector_size])
            if s >= len(mini_fat):
                break
            s = mini_fat[s]
        return b"".join(chunks)

    entries_start = 512 + root_start * sector_size
    target = stream_name.encode("utf-16le")
    for i in range(0, sector_size, 128):
        off = entries_start + i
        if off + 128 > len(data):
            break
        name_raw = data[off : off + 64]
        name = name_raw.decode("utf-16le", errors="ignore").rstrip("\x00")
        if name != stream_name:
            continue
        size = struct.unpack_from("<I", data, off + 120)[0]
        start_sec = struct.unpack_from("<I", data, off + 116)[0]
        if size >= mini_stream_cutoff:
            payload = read_chain(start_sec)
        else:
            root = read_chain(root_start)
            payload = read_mini_chain(start_sec, root)
        return payload[:size]
    return None


def extract_utf16le_runs(blob: bytes, min_chars: int = 2) -> List[str]:
    runs: List[str] = []
    i = 0
    n = len(blob)
    while i < n - 1:
        chars: List[str] = []
        start = i
        while i + 1 < n:
            code = blob[i] | (blob[i + 1] << 8)
            if code == 0:
                i += 2
                break
            ok = (
                code in (0x09, 0x0A, 0x0D, 0x20)
                or (0x21 <= code < 0xD800)
                or (0xE000 <= code <= 0xFFFD)
            )
            if not ok:
                break
            chars.append(chr(code))
            i += 2
        text = re.sub(r"[ \t]+", " ", "".join(chars)).strip()
        if len(text) >= min_chars and _is_meaningful_text(text):
            runs.append(text)
        if i == start:
            i += 1
    return runs


def _is_meaningful_text(text: str) -> bool:
    t = text.strip()
    if len(t) < 2:
        return False
    if re.fullmatch(r"[\d\s.,;:\-()[\]{}\\/|+=_*#@!%^&~`'\"<>?]+", t):
        return False

    # OLE \u4e8c\u8fdb\u5236\u88ab\u8bef\u8bfb\u4e3a UTF-16LE \u65f6\u4f1a\u4ea7\u751f\u5927\u91cf\u5f02\u5e38\u5b57\u7b26\uff08PUA/\u97e9\u6587/\u897f\u91cc\u5c14\u7b49\uff09\uff0c
    # \u7ed9\u5b83\u4eec\u6263\u5206\uff1b\u771f\u5b9e\u4e2d\u6587\u6b63\u6587\u57fa\u7840 CJK \u5360\u6bd4\u9ad8\u3001\u5f97\u5206\u9ad8\u3002
    def _text_score(s: str) -> float:
        score = 0.0
        for ch in s:
            o = ord(ch)
            if 0x4E00 <= o <= 0x9FFF:
                score += 3.0
            elif 0x3400 <= o <= 0x4DBF:
                score += 1.5
            elif 0x3040 <= o <= 0x30FF:
                score += 2.0
            elif ch in "\uff0c\u3002\uff1b\uff1a\u3001\uff08\uff09\u3010\u3011\u300a\u300b\u3008\u3009\u300c\u300d\u300e\u300f\u201c\u201d\u2018\u2019\uff01\uff1f\u2014\u2026\u00b7\uff05\uffe5" or o in (0x3000, 0x2013):
                score += 2.0
            elif ch.isascii() and ch.isalnum():
                score += 1.0
            elif ch in " \t\r\n.":
                score += 0.5
            elif 0xE000 <= o <= 0xF8FF:
                score -= 6.0
            elif 0xAC00 <= o <= 0xD7A3:
                score -= 4.0
            elif 0x0400 <= o <= 0x052F:
                score -= 4.0
            else:
                score -= 3.0
        return score

    base_cjk = sum(1 for ch in t if 0x4E00 <= ord(ch) <= 0x9FFF)
    cjk_ratio = base_cjk / len(t) if t else 0
    # 二进制误读的 run 往往很短，真实正文通常是一整段
    if cjk_ratio >= 0.45 and len(t) >= 4:
        return _text_score(t) >= len(t) * 1.2 or len(t) >= 60

    ascii_alnum = sum(1 for ch in t if ch.isascii() and ch.isalnum())
    if ascii_alnum / len(t) >= 0.6 and _text_score(t) > 0 and len(t) >= 4:
        return True
    return len(t) >= 12 and _text_score(t) >= len(t)


def dedupe_lines(lines: Iterable[str]) -> str:
    seen = set()
    out: List[str] = []
    for line in lines:
        k = line.strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return "\n".join(out)


def extract_doc_text(file_path: Path) -> str:
    data = file_path.read_bytes()
    if data[:8] != _OLE_MAGIC:
        raise ValueError("not a valid .doc OLE file")

    for stream in ("WordDocument", "1Table", "0Table"):
        payload = read_ole_stream(data, stream)
        if not payload:
            continue
        if stream == "WordDocument":
            text = dedupe_lines(extract_utf16le_runs(payload, 2))
            if text.strip():
                return text

    # 兜底：全文件 UTF-16LE 扫描
    fallback = dedupe_lines(extract_utf16le_runs(data, 4))
    if fallback.strip():
        return fallback
    raise ValueError("no readable text extracted from .doc")


def extract_ppt_text(file_path: Path) -> str:
    data = file_path.read_bytes()
    if data[:8] != _OLE_MAGIC:
        raise ValueError("not a valid .ppt OLE file")

    # 优先读正文流，其次 Data 流；取 UTF-16LE 文本片段得分最高者
    best = ""
    best_score = 0
    for stream in ("PowerPoint Document", "Data"):
        payload = read_ole_stream(data, stream)
        if not payload:
            continue
        text = dedupe_lines(extract_utf16le_runs(payload, 2))
        score = _score_extracted_text(text)
        if score > best_score:
            best_score = score
            best = text
    if best.strip():
        return best

    # 兜底：全文件 UTF-16LE 扫描
    fallback = dedupe_lines(extract_utf16le_runs(data, 4))
    if fallback.strip():
        return fallback
    raise ValueError("no readable text extracted from .ppt")


def _score_extracted_text(text: str) -> int:
    total = len(text.strip())
    if total == 0:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    latin = sum(1 for ch in text if ch.isalpha() and ch.isascii())
    digits = sum(1 for ch in text if ch.isdigit())
    return cjk + latin + digits


def _xlsx_col_letters(cell_ref: str) -> Tuple[int, int]:
    m = re.match(r"([A-Z]+)(\d+)", cell_ref.upper())
    if not m:
        return 0, 0
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col


def extract_xlsx_text(file_path: Path) -> str:
    lines: List[str] = []
    with zipfile.ZipFile(file_path, "r") as zf:
        shared: List[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{_NS_MAIN}si"):
                parts = [t.text or "" for t in si.iter(f"{_NS_MAIN}t")]
                shared.append("".join(parts))

        sheet_files = sorted(
            n for n in zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
        )
        for idx, sheet_path in enumerate(sheet_files, start=1):
            root = ET.fromstring(zf.read(sheet_path))
            sheet_name = f"Sheet{idx}"
            lines.append(f"[sheet:{sheet_name}]")
            rows: Dict[int, Dict[int, str]] = {}
            for cell in root.findall(f".//{_NS_MAIN}c"):
                ref = cell.attrib.get("r", "")
                row_i, col_i = _xlsx_col_letters(ref)
                if row_i <= 0:
                    continue
                cell_type = cell.attrib.get("t")
                value = ""
                if cell_type == "s":
                    v = cell.find(f"{_NS_MAIN}v")
                    if v is not None and v.text is not None:
                        try:
                            value = shared[int(v.text)]
                        except (ValueError, IndexError):
                            value = v.text
                elif cell_type == "inlineStr":
                    t = cell.find(f".//{_NS_MAIN}t")
                    value = (t.text or "") if t is not None else ""
                else:
                    v = cell.find(f"{_NS_MAIN}v")
                    value = (v.text or "") if v is not None else ""
                value = str(value).strip()
                if value:
                    rows.setdefault(row_i, {})[col_i] = value
            for row_i in sorted(rows):
                cells = [rows[row_i][c] for c in sorted(rows[row_i])]
                lines.append(" | ".join(cells))
    text = "\n".join(lines).strip()
    if not text:
        raise ValueError("no readable text extracted from .xlsx")
    return text


def _decode_rk(rk: int) -> str:
    is_int = rk & 2
    is_x100 = rk & 1
    if is_int:
        val = rk >> 2
        if val & 0x20000000:
            val = val - 0x40000000
        num = float(val)
    else:
        raw = struct.pack("<I", rk & 0xFFFFFFFC)
        num = struct.unpack("<d", raw)[0]
    if is_x100:
        num /= 100.0
    if float(num).is_integer():
        return str(int(num))
    return str(num)


def _parse_xls_sst(data: bytes, offset: int, length: int) -> List[str]:
    end = offset + length
    if offset + 8 > end:
        return []
    total, unique = struct.unpack_from("<II", data, offset + 4)
    strings: List[str] = []
    pos = offset + 8
    while pos < end and len(strings) < unique:
        if pos >= len(data):
            break
        flags = data[pos + 1] if pos + 1 < len(data) else 0
        is_unicode = flags & 1
        is_ext = flags & 4
        is_rich = flags & 8
        pos += 2
        if pos + 2 > end:
            break
        n_chars = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        if is_rich:
            pos += 4
        if is_ext:
            if pos + 4 > end:
                break
            ext_len = struct.unpack_from("<I", data, pos)[0]
            pos += 4 + ext_len
        if is_unicode:
            byte_len = n_chars * 2
            raw = data[pos : pos + byte_len]
            pos += byte_len
            strings.append(raw.decode("utf-16le", errors="replace"))
        else:
            raw = data[pos : pos + n_chars]
            pos += n_chars
            strings.append(raw.decode("latin-1", errors="replace"))
    return strings


def _parse_xls_sheet(
    data: bytes,
    offset: int,
    sst: List[str],
    lines: List[str],
    sheet_name: str,
) -> None:
    lines.append(f"[sheet:{sheet_name}]")
    pos = offset
    end = len(data)
    row_cells: Dict[int, Dict[int, str]] = {}
    while pos + 4 <= end:
        rtype, rlen = struct.unpack_from("<HH", data, pos)
        pos += 4
        if pos + rlen > end:
            break
        body = data[pos : pos + rlen]
        pos += rlen
        if rtype == 0x000A:  # EOF
            break
        if rtype == 0x00FD and len(body) >= 10:  # LABELSST
            row, col, _xf, sst_idx = struct.unpack_from("<HHHI", body)
            if 0 <= sst_idx < len(sst):
                row_cells.setdefault(row, {})[col] = sst[sst_idx].strip()
        elif rtype == 0x0203 and len(body) >= 14:  # NUMBER
            row, col, _xf = struct.unpack_from("<HHH", body)
            num = struct.unpack_from("<d", body, 6)[0]
            val = str(int(num)) if float(num).is_integer() else str(num)
            row_cells.setdefault(row, {})[col] = val
        elif rtype == 0x027E and len(body) >= 10:  # RK
            row, col, _xf, rk = struct.unpack_from("<HHHI", body)
            row_cells.setdefault(row, {})[col] = _decode_rk(rk)
        elif rtype == 0x00BD and len(body) >= 6:  # MULRK
            row, col_first = struct.unpack_from("<HH", body)
            p = 4
            while p + 6 <= len(body) - 2:
                _xf, rk = struct.unpack_from("<HI", body, p)
                row_cells.setdefault(row, {})[col_first] = _decode_rk(rk)
                col_first += 1
                p += 6
    for row_i in sorted(row_cells):
        cells = [row_cells[row_i][c] for c in sorted(row_cells[row_i]) if row_cells[row_i][c]]
        if cells:
            lines.append(" | ".join(cells))


def extract_xls_text(file_path: Path) -> str:
    data = file_path.read_bytes()
    workbook = read_ole_stream(data, "Workbook") or read_ole_stream(data, "Book")
    if not workbook:
        raise ValueError("Workbook stream not found in .xls")

    sst: List[str] = []
    sheets: List[Tuple[str, int]] = []
    pos = 0
    end = len(workbook)
    while pos + 4 <= end:
        rtype, rlen = struct.unpack_from("<HH", workbook, pos)
        pos += 4
        if pos + rlen > end:
            break
        body = workbook[pos : pos + rlen]
        pos += rlen
        if rtype == 0x00FC:  # SST
            sst = _parse_xls_sst(workbook, pos - 4 - rlen, rlen + 4)
        elif rtype == 0x0085 and len(body) >= 8:  # BOUNDSHEET
            offset = struct.unpack_from("<I", body, 0)[0]
            cch = body[6]
            name_start = 7
            if name_start < len(body) and (body[name_start] & 1):
                name = body[name_start + 1 : name_start + 1 + cch * 2].decode("utf-16le", errors="replace")
            else:
                name = body[name_start + 1 : name_start + 1 + cch].decode("latin-1", errors="replace")
            sheets.append((name.strip() or f"Sheet{len(sheets) + 1}", offset))

    lines: List[str] = []
    if sheets:
        for name, off in sheets:
            _parse_xls_sheet(workbook, off, sst, lines, name)
    else:
        _parse_xls_sheet(workbook, 0, sst, lines, "Sheet1")

    text = "\n".join(lines).strip()
    if not text:
        raise ValueError("no readable text extracted from .xls")
    return text
