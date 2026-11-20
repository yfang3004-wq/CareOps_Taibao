"""Bounded CSV/TSV and static OOXML table interchange, not an Excel engine.

No macros, external relationships, formula execution, ZIP extraction or arbitrary paths.
The code is an application transport codec; model-produced workbook fixtures use
artifact_tool. Only ordinary value tables are accepted, with explicit rejection
and source-row diagnostics for unsupported cells. No third-party runtime dependency.
"""
from __future__ import annotations
import base64
import binascii
import csv
import io
import math
import posixpath
import re
import zipfile
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

MAX_FILE = 2_000_000
MAX_EXPANDED = 12_000_000
MAX_ROWS = 1000
MAX_COLS = 80
MAX_SHEETS = 24
MAX_CELLS = 60000
MAX_TEXT = 12000
MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'


def clean_filename(name: str) -> str:
    # Never use an uploaded filename as a filesystem path.
    name = name.replace('\\', '/').split('/')[-1]
    name = re.sub(r'[\x00-\x1f\x7f]', '', name).strip()
    if not name or len(name) > 160:
        raise ValueError('文件名为空或超过160字符')
    return name


def unpack_file(filename: str, file_base64: str) -> tuple[str, bytes]:
    name = clean_filename(filename)
    if not name.lower().endswith(('.csv', '.tsv', '.xlsx')):
        raise ValueError('只接受普通 .csv、.tsv、.xlsx；.xls、宏、加密文件请另存为值表')
    if len(file_base64) > (MAX_FILE + 2) // 3 * 4:
        raise ValueError('文件超过2MB本轮限制')
    try:
        data = base64.b64decode(file_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('文件传输编码无效') from exc
    if not data or len(data) > MAX_FILE:
        raise ValueError('文件为空或超过2MB')
    return name, data


def text_xml(data: bytes) -> ET.Element:
    # Accept only UTF-8 OOXML XML parts. This also avoids UTF-16 DTD bypasses.
    if b'\x00' in data or re.search(br'<!\s*(DOCTYPE|ENTITY)', data, flags=re.I):
        raise ValueError('不接受DTD、实体声明或非UTF-8工作簿XML')
    try:
        return ET.fromstring(data.decode('utf-8-sig'))
    except (UnicodeDecodeError, ET.ParseError, RecursionError) as exc:
        raise ValueError('工作簿XML格式无效') from exc


def col_number(letters: str) -> int:
    n = 0
    for c in letters:
        n = n * 26 + ord(c) - 64
    return n - 1


def col_name(n: int) -> str:
    out = ''
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def decode_csv(data: bytes, name: str) -> dict:
    encoding = 'UTF-8'
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = 'UTF-16'
        try: text = data.decode('utf-16')
        except UnicodeError as exc: raise ValueError('UTF-16文本无效') from exc
    else:
        try: text = data.decode('utf-8-sig')
        except UnicodeError:
            encoding = 'GB18030（请核对中文）'
            try: text = data.decode('gb18030')
            except UnicodeError as exc: raise ValueError('无法解码文本，请另存为UTF-8 CSV') from exc
    if '\x00' in text:
        raise ValueError('文本含NUL，不是普通CSV')
    if name.lower().endswith('.tsv'):
        delimiter = '\t'
    else:
        try: delimiter = csv.Sniffer().sniff(text[:12000], delimiters=',;\t').delimiter
        except csv.Error: delimiter = ','
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
    rows, errors, previous, cells = [], {}, 0, 0
    try:
        for values in reader:
            line = previous + 1; previous = reader.line_num
            if len(values) > MAX_COLS:
                raise ValueError('单表超过80列，请拆分')
            if not any(v.strip() for v in values): continue
            cells += len(values)
            if cells > MAX_CELLS or len(rows) >= MAX_ROWS + 20:
                raise ValueError('单表超过1000条数据加20行表头/说明限制')
            if any(len(v) > MAX_TEXT for v in values):
                errors[str(line)] = ['字段超过12000字符']
            rows.append({'line': line, 'values': values})
    except csv.Error as exc:
        raise ValueError('CSV引号或字段格式无效，请另存后重试') from exc
    if not rows: raise ValueError('文件没有非空数据')
    return {'sheets': [{'name': 'CSV', 'rows': rows, 'errors': errors}], 'warnings': [], 'encoding': encoding}


def _excel_value(value: str, fmt: str, format_id: int, date1904: bool):
    if len(value) > 1000: raise ValueError('数值单元格文本过长')
    try: number = Decimal(value)
    except InvalidOperation as exc: raise ValueError('非法数值单元格') from exc
    if not number.is_finite(): raise ValueError('非有限数值不允许导入')
    if number and not -308 <= number.adjusted() <= 308:
        raise ValueError('数值指数超出本轮范围，请使用文本编号')
    nf = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', '', fmt)
    is_date = format_id in set(range(14, 18)) | {22} | set(range(27, 37)) | set(range(50, 59)) or bool(re.search('[yd]', nf, re.I))
    if is_date:
        f = float(number)
        if f < 0 or f > 2958465: raise ValueError('Excel日期序号超出支持范围')
        if not date1904 and int(f) == 60: raise ValueError('Excel虚构日期1900-02-29不能导入')
        base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 31)
        if not date1904 and f >= 61: f -= 1
        dt = base + timedelta(seconds=round(f * 86400))
        return dt.strftime('%Y-%m-%d %H:%M:%S') if dt.time() != datetime.min.time() or re.search('[hs]', nf, re.I) else dt.strftime('%Y-%m-%d')
    if number == number.to_integral_value():
        # Excel numeric cells cannot faithfully carry >15 significant integer digits.
        if len(str(abs(int(number)))) > 15: raise ValueError('超过15位的数值编号可能已经丢失精度，请在源文件使用文本')
        if re.fullmatch(r'0{2,30}', fmt): return str(int(number)).zfill(len(fmt))
        return int(number)
    f = float(number)
    if not math.isfinite(f): raise ValueError('数值超出范围')
    return f


def decode_xlsx(data: bytes) -> dict:
    try: z = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc: raise ValueError('不是有效的XLSX压缩容器') from exc
    with z:
        infos = z.infolist()
        if len(infos) > 600 or len({i.filename for i in infos}) != len(infos):
            raise ValueError('工作簿部件过多或存在重名部件')
        total = 0
        for i in infos:
            n = i.filename
            if '\\' in n or n.startswith('/') or '..' in n.split('/') or i.flag_bits & 1:
                raise ValueError('拒绝不安全路径或加密工作簿')
            if i.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ValueError('不支持此工作簿压缩方式')
            if re.search(r'vbaProject|externalLinks/|embeddings/|activeX/', n, re.I):
                raise ValueError('不支持宏、嵌入对象或外部链接')
            total += i.file_size
            if total > MAX_EXPANDED or i.file_size > MAX_EXPANDED or (i.file_size > 100000 and i.file_size / max(1, i.compress_size) > 250):
                raise ValueError('工作簿展开体积或压缩比超过安全限额')
        names = {i.filename for i in infos}
        def get(n, optional=False):
            if n not in names:
                if optional: return None
                raise ValueError('缺少工作簿部件：' + n)
            try: return text_xml(z.read(n))
            except (zipfile.BadZipFile, RuntimeError, OSError) as exc: raise ValueError('工作簿部件损坏') from exc
        # External relationships are not followed and are explicitly rejected.
        for n in names:
            if n.endswith('.rels'):
                for r in get(n):
                    if r.get('TargetMode') == 'External':
                        raise ValueError('普通台账导入不接受外部关系；请另存为无链接值表')
        wb = get('xl/workbook.xml'); rel = get('xl/_rels/workbook.xml.rels')
        relations = {}
        for r in rel:
            target = r.get('Target', '')
            target = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
            if not target.startswith('xl/') or '..' in target.split('/') or '://' in target:
                raise ValueError('工作表关系路径无效')
            relations[r.get('Id')] = target
        shared_root = get('xl/sharedStrings.xml', True)
        shared = []
        if shared_root is not None:
            for si in shared_root.findall(f'{{{MAIN}}}si'):
                shared.append(''.join(t.text or '' for t in si.iter(f'{{{MAIN}}}t')))
                if len(shared) > MAX_CELLS: raise ValueError('共享字符串过多')
        style = get('xl/styles.xml', True); formats, xfs = {}, []
        if style is not None:
            for f in style.iter(f'{{{MAIN}}}numFmt'):
                formats[int(f.get('numFmtId', '0'))] = f.get('formatCode', '')
            node = style.find(f'{{{MAIN}}}cellXfs')
            if node is not None: xfs = [int(f.get('numFmtId', '0')) for f in node]
        prop = wb.find(f'{{{MAIN}}}workbookPr')
        date1904 = prop is not None and prop.get('date1904') in {'1', 'true'}
        defs = wb.findall(f'{{{MAIN}}}sheets/{{{MAIN}}}sheet')
        if len(defs) > MAX_SHEETS: raise ValueError('工作表超过24张')
        out, warnings, cell_count = [], [], 0
        for sh in defs:
            name = sh.get('name', '未命名')
            if sh.get('state') in {'hidden', 'veryHidden'}:
                warnings.append(name + '：隐藏工作表未读取'); continue
            target = relations.get(sh.get(f'{{{REL}}}id'))
            if not target: raise ValueError('工作表关系缺失')
            root = get(target); sheet_data = root.find(f'{{{MAIN}}}sheetData')
            if sheet_data is None: raise ValueError('仅支持普通单元格工作表')
            rows, errors, seen_lines = [], {}, set()
            for row in sheet_data:
                line = int(row.get('r', len(rows) + 1))
                if line < 1 or line > 1048576 or line in seen_lines: raise ValueError('工作表行号无效或重复')
                seen_lines.add(line); cells = {}; rowerrors = []
                if row.get('hidden') == '1': warnings.append(name + f'：第{line}隐藏行仍按源数据读取')
                for c in row.findall(f'{{{MAIN}}}c'):
                    m = re.fullmatch(r'([A-Z]{1,3})([0-9]+)', c.get('r', ''))
                    if not m or int(m[2]) != line: raise ValueError('单元格坐标无效')
                    ix = col_number(m[1])
                    if ix >= MAX_COLS: raise ValueError('数据超过80列')
                    if ix in cells: raise ValueError('重复单元格坐标')
                    cell_count += 1
                    if cell_count > MAX_CELLS: raise ValueError('工作簿超过60000个单元格')
                    value = c.findtext(f'{{{MAIN}}}v', default=''); typ = c.get('t', '')
                    try:
                        if c.find(f'{{{MAIN}}}f') is not None:
                            raise ValueError('公式单元格不计算也不采用缓存值，请先另存为值')
                        if typ == 's':
                            n = int(value)
                            if n < 0 or n >= len(shared): raise ValueError('共享字符串索引无效')
                            value = shared[n]
                        elif typ == 'inlineStr': value = ''.join(t.text or '' for t in c.iter(f'{{{MAIN}}}t'))
                        elif typ == 'b': value = 'TRUE' if value == '1' else 'FALSE'
                        elif typ == 'e': raise ValueError('源单元格包含错误值：' + value[:30])
                        elif typ == 'd': value = value.replace('T', ' ').rstrip('Z')
                        elif value != '' and typ not in {'str'}:
                            si = int(c.get('s', '0'))
                            fmt_id = xfs[si] if 0 <= si < len(xfs) else 0
                            value = _excel_value(value, formats.get(fmt_id, ''), fmt_id, date1904)
                        if isinstance(value, str) and len(value) > MAX_TEXT: raise ValueError('字段超过12000字符')
                    except (ValueError, IndexError, OverflowError) as exc:
                        rowerrors.append(m[1] + str(line) + '：' + str(exc)); value = '[不接受的源单元格]'
                    cells[ix] = value
                if any(str(v).strip() for v in cells.values()):
                    rows.append({'line': line, 'values': [cells.get(k, '') for k in range(max(cells) + 1)]})
                    if rowerrors: errors[str(line)] = rowerrors
                    if len(rows) > MAX_ROWS + 20: raise ValueError(name + '超过1000条数据加20行表头/说明限制')
            if root.find(f'{{{MAIN}}}mergeCells') is not None:
                warnings.append(name + '：合并单元格只取左上角，不自动填充')
            if rows: out.append({'name': name, 'rows': rows, 'errors': errors})
        if not out: raise ValueError('未找到可读取的非空工作表')
        return {'sheets': out, 'warnings': warnings[:100], 'encoding': 'XLSX（普通值表）'}


def read_file(filename: str, file_base64: str) -> tuple[str, bytes, dict]:
    name, data = unpack_file(filename, file_base64)
    try: book = decode_xlsx(data) if name.lower().endswith('.xlsx') else decode_csv(data, name)
    except (ValueError, OverflowError, KeyError, RecursionError) as exc:
        raise ValueError(str(exc)[:500]) from exc
    return name, data, book


def safe_csv_cell(value):
    s = str(value if value is not None else '')
    # Data quoted by csv.writer is still executable as a formula in spreadsheet apps.
    if isinstance(value, str) and re.match(r'^[\s\ufeff]*[=+\-@\t\r\n]', s):
        return "'" + s
    return s


def export_csv(headers: list[str], rows: list[list]) -> bytes:
    out = io.StringIO(newline=''); w = csv.writer(out, lineterminator='\r\n', quoting=csv.QUOTE_ALL)
    w.writerow([safe_csv_cell(x) for x in headers])
    for row in rows: w.writerow([safe_csv_cell(x) for x in row])
    return ('\ufeff' + out.getvalue()).encode('utf-8')


def _xml_text(value):
    text = str(value if value is not None else '')
    if any(ord(x) < 32 and x not in '\t\n\r' for x in text):
        raise ValueError('导出内容含不支持的控制字符；请核对原始记录')
    if len(text) > 32767: raise ValueError('导出字段超过Excel单元格长度限制')
    return escape(text)


def export_xlsx(headers: list[str], rows: list[list], metadata: dict) -> bytes:
    """Write a simple styled value-only OOXML report. No f/formula nodes, ever."""
    ns = f'xmlns="{MAIN}"'
    def display_width(value):
        return sum(2 if unicodedata.east_asian_width(c) in {'W','F'} else 1 for c in str(value or ''))
    widths=[]
    for k,h in enumerate(headers):
        sampled=max([display_width(h)+6]+[max([display_width(line) for line in str(r[k] if k<len(r) else '').splitlines()] or [0])+2 for r in rows[:200]])
        widths.append(min(38,max(18,sampled)))
    def sheet_xml(grid, widths):
        parts = [f'<worksheet {ns}><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="28"/><cols>']
        parts += [f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths)]
        parts.append('</cols><sheetData>')
        for n, row in enumerate(grid, 1):
            lines=max([1]+[sum(max(1,math.ceil(display_width(line)/max(1,widths[k]-2))) for line in str(value or '').split('\n')) for k,value in enumerate(row)])
            height=min(180,max(30 if n==1 else 32,lines*16+10))
            parts.append(f'<row r="{n}" ht="{height}" customHeight="1">')
            for k, value in enumerate(row, 1):
                loc = col_name(k) + str(n)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    parts.append(f'<c r="{loc}" s="2"><v>{value}</v></c>')
                else:
                    parts.append(f'<c r="{loc}" s="{1 if n == 1 else 3}" t="inlineStr"><is><t xml:space="preserve">{_xml_text(value)}</t></is></c>')
            parts.append('</row>')
        parts.append('</sheetData>')
        if len(grid) > 1: parts.append(f'<autoFilter ref="A1:{col_name(len(grid[0]))}{len(grid)}"/>')
        parts.append('</worksheet>'); return ''.join(parts)
    styles = f'''<styleSheet {ns}><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF235F4E"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="49" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''
    ct = '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'''
    rels = f'<Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    wb = f'<workbook {ns} xmlns:r="{REL}"><sheets><sheet name="数据" sheetId="1" r:id="rId1"/><sheet name="导出说明" sheetId="2" r:id="rId2"/></sheets></workbook>'
    wr = f'<Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{REL}/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="{REL}/styles" Target="styles.xml"/></Relationships>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        contents = {'[Content_Types].xml': ct, '_rels/.rels': rels, 'xl/workbook.xml': wb, 'xl/_rels/workbook.xml.rels': wr, 'xl/styles.xml': styles,
                    'xl/worksheets/sheet1.xml': sheet_xml([headers] + rows, widths),
                    'xl/worksheets/sheet2.xml': sheet_xml([['项目', '内容']] + [[k, v] for k, v in metadata.items()], [25, 65])}
        for n, v in contents.items():
            z.writestr(n, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + v)
    return buf.getvalue()
