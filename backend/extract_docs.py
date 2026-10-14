import sys, os, zipfile
import xml.etree.ElementTree as ET

def ln(tag):
    return tag.split('}')[-1] if '}' in tag else tag

def extract_docx(path):
    out = []
    with zipfile.ZipFile(path) as z:
        parts = ['word/document.xml']
        parts += sorted(n for n in z.namelist() if n.startswith('word/header') or n.startswith('word/footer'))
        for name in parts:
            if name not in z.namelist():
                continue
            root = ET.fromstring(z.read(name))
            for el in root.iter():
                if ln(el.tag) == 'p':
                    texts = []
                    for t in el.iter():
                        lt = ln(t.tag)
                        if lt == 't':
                            texts.append(t.text or '')
                        elif lt == 'tab':
                            texts.append('\t')
                    line = ''.join(texts).strip()
                    if line:
                        out.append(line)
    return '\n'.join(out)

def extract_pptx(path):
    out = []
    with zipfile.ZipFile(path) as z:
        def num(n):
            d = ''.join(filter(str.isdigit, n.split('/')[-1]))
            return int(d) if d else 0
        slides = sorted((n for n in z.namelist()
                         if n.startswith('ppt/slides/slide') and n.endswith('.xml')), key=num)
        for i, name in enumerate(slides, 1):
            root = ET.fromstring(z.read(name))
            texts = [ (t.text or '') for t in root.iter() if ln(t.tag) == 't' ]
            body = '\n'.join(x for x in texts if x.strip())
            out.append(f"----- Slide {i} -----\n{body}")
    return '\n\n'.join(out)

def extract_xlsx(path):
    out = []
    with zipfile.ZipFile(path) as z:
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            root = ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in root:
                if ln(si.tag) == 'si':
                    shared.append(''.join(t.text or '' for t in si.iter() if ln(t.tag) == 't'))
        names = []
        if 'xl/workbook.xml' in z.namelist():
            wb = ET.fromstring(z.read('xl/workbook.xml'))
            for el in wb.iter():
                if ln(el.tag) == 'sheet':
                    names.append(el.get('name', ''))
        def num(n):
            d = ''.join(filter(str.isdigit, n.split('/')[-1]))
            return int(d) if d else 0
        sheets = sorted((n for n in z.namelist()
                         if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')), key=num)
        for idx, name in enumerate(sheets):
            title = names[idx] if idx < len(names) else name
            root = ET.fromstring(z.read(name))
            out.append(f"===== SHEET: {title} =====")
            for row in root.iter():
                if ln(row.tag) != 'row':
                    continue
                cells = []
                for c in row:
                    if ln(c.tag) != 'c':
                        continue
                    t = c.get('t'); v = None
                    for child in c:
                        cl = ln(child.tag)
                        if cl == 'v':
                            v = child.text
                        elif cl == 'is':
                            v = ''.join(x.text or '' for x in child.iter() if ln(x.tag) == 't')
                    if v is None:
                        cells.append(''); continue
                    if t == 's':
                        try: cells.append(shared[int(v)])
                        except Exception: cells.append(v)
                    else:
                        cells.append(v)
                if any(x.strip() for x in cells):
                    out.append(' | '.join(cells))
    return '\n'.join(out)

def main():
    src = sys.argv[1]
    dst = sys.argv[2]
    os.makedirs(dst, exist_ok=True)
    for fn in sorted(os.listdir(src)):
        p = os.path.join(src, fn)
        low = fn.lower()
        try:
            if low.endswith('.docx'):
                text = extract_docx(p)
            elif low.endswith('.pptx'):
                text = extract_pptx(p)
            elif low.endswith('.xlsx'):
                text = extract_xlsx(p)
            else:
                continue
        except Exception as e:
            text = f"[ERROR extracting {fn}: {e}]"
        outp = os.path.join(dst, fn + '.txt')
        with open(outp, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"{fn}: {len(text)} chars -> {outp}")

if __name__ == '__main__':
    main()
