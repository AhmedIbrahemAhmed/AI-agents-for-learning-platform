import sys
from pathlib import Path
import zipfile
import re
import hashlib

FILES = [
    Path('Graduation_Project_AI_System_Architecture_Specification.docx'),
    Path('Graduation_Project_AI_System_Architecture_Specification2.docx'),
    Path('Graduation_Project_AI_System_Architecture_Specification.pdf'),
]


def sha256_of_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for chunk in iter(lambda: fh.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def extract_text_from_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path, 'r') as z:
            with z.open('word/document.xml') as docxml:
                raw = docxml.read().decode('utf-8', errors='ignore')
                # remove xml tags, keep text
                text = re.sub(r'<(/?w:p[^>]*)>', '\n', raw)
                text = re.sub(r'<[^>]+>', '', text)
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                return '\n'.join(lines[:30])
    except Exception as e:
        return f'ERROR extracting docx: {e}'


def extract_text_from_pdf_bytes(path: Path) -> str:
    try:
        with open(path, 'rb') as fh:
            data = fh.read(200000)  # first 200KB
            # find ascii runs
            runs = re.findall(rb'[\x20-\x7E]{30,}', data)
            if not runs:
                return ''
            sample = runs[0].decode('utf-8', errors='ignore')
            return sample[:3000]
    except Exception as e:
        return f'ERROR reading pdf bytes: {e}'


def main():
    results = []
    for f in FILES:
        if not f.exists():
            print(f'Missing file: {f}')
            continue
        size = f.stat().st_size
        digest = sha256_of_file(f)
        if f.suffix.lower() == '.docx':
            sample = extract_text_from_docx(f)
            results.append({'path': str(f), 'type': 'docx', 'size': size, 'sha256': digest, 'sample': sample})
        elif f.suffix.lower() == '.pdf':
            sample = extract_text_from_pdf_bytes(f)
            results.append({'path': str(f), 'type': 'pdf', 'size': size, 'sha256': digest, 'sample': sample})

    for r in results:
        print('---')
        print('File:', r['path'])
        print('Type:', r['type'].upper())
        print('Size (bytes):', r['size'])
        print('SHA256:', r['sha256'])
        print('Sample:\n')
        print(r['sample'][:2000])


if __name__ == '__main__':
    main()
import sys
from pathlib import Path

try:
    from docx import Document
except Exception as e:
    print('MISSING: python-docx')
    raise

try:
    import PyPDF2
except Exception as e:
    print('MISSING: PyPDF2')
    raise

FILES = [
    Path('Graduation_Project_AI_System_Architecture_Specification.docx'),
    Path('Graduation_Project_AI_System_Architecture_Specification2.docx'),
    Path('Graduation_Project_AI_System_Architecture_Specification.pdf'),
]


def summarize_docx(path: Path):
    doc = Document(path)
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    import sys
    from pathlib import Path
    import zipfile
    import re
    import hashlib

    FILES = [
        Path('Graduation_Project_AI_System_Architecture_Specification.docx'),
        Path('Graduation_Project_AI_System_Architecture_Specification2.docx'),
        Path('Graduation_Project_AI_System_Architecture_Specification.pdf'),
    ]


    def sha256_of_file(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, 'rb') as fh:
            for chunk in iter(lambda: fh.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()


    def extract_text_from_docx(path: Path) -> str:
        try:
            with zipfile.ZipFile(path, 'r') as z:
                with z.open('word/document.xml') as docxml:
                    raw = docxml.read().decode('utf-8', errors='ignore')
                    # remove xml tags, keep text
                    text = re.sub(r'<(/?w:p[^>]*)>', '\n', raw)
                    text = re.sub(r'<[^>]+>', '', text)
                    lines = [l.strip() for l in text.splitlines() if l.strip()]
                    return '\n'.join(lines[:30])
        except Exception as e:
            return f'ERROR extracting docx: {e}'


    def extract_text_from_pdf_bytes(path: Path) -> str:
        try:
            with open(path, 'rb') as fh:
                data = fh.read(200000)  # first 200KB
                # find ascii runs
                runs = re.findall(rb'[\x20-\x7E]{30,}', data)
                if not runs:
                    return ''
                sample = runs[0].decode('utf-8', errors='ignore')
                return sample[:3000]
        except Exception as e:
            return f'ERROR reading pdf bytes: {e}'


    def main():
        results = []
        for f in FILES:
            if not f.exists():
                print(f'Missing file: {f}')
                continue
            size = f.stat().st_size
            digest = sha256_of_file(f)
            if f.suffix.lower() == '.docx':
                sample = extract_text_from_docx(f)
                results.append({'path': str(f), 'type': 'docx', 'size': size, 'sha256': digest, 'sample': sample})
            elif f.suffix.lower() == '.pdf':
                sample = extract_text_from_pdf_bytes(f)
                results.append({'path': str(f), 'type': 'pdf', 'size': size, 'sha256': digest, 'sample': sample})

        for r in results:
            print('---')
            print('File:', r['path'])
            print('Type:', r['type'].upper())
            print('Size (bytes):', r['size'])
            print('SHA256:', r['sha256'])
            print('Sample:\n')
            print(r['sample'][:2000])


    if __name__ == '__main__':
        main()
