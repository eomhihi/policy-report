import os
import re
import subprocess
import requests
import psycopg2
from pypdf import PdfReader

# ==========================================
# [설정 영역] 본인의 환경에 맞게 직접 채워 넣어주세요!
# ==========================================
DB_CONFIG = {
    "host": "POSTGRES_HOST_값",
    "database": "POSTGRES_DATABASE_값",
    "user": "POSTGRES_USER_값",
    "password": "POSTGRES_PASSWORD_값",
    "port": "5432",
    "sslmode": "require"
}

# 네이버 클OVA OCR API 설정 정보
CLOVA_OCR_URL = "YOUR_CLOVA_OCR_INVOKE_URL"
CLOVA_OCR_SECRET = "YOUR_CLOVA_OCR_SECRET_KEY"

# 처리할 샘플 보고서 리스트 규격화
sample_reports = [
    {
        "doc_id": 20260001,
        "filename": "20260001_세종시4차산업혁명_SJTP_2025_미래산업.pdf",
        "doc_title": "세종시 4차산업혁명 대응 기본계획 및 미래산업 동향",
        "inst_code": "SJTP",
        "pub_year": 2025,
        "industry_tag": "4차산업혁명,미래산업"
    },
    {
        "doc_id": 20260002,
        "filename": "20260002_미래전략도시세종_SJDI_2025_미래산업.pdf",
        "doc_title": "미래전략도시 세종 구축을 위한 미래산업 육성 방안",
        "inst_code": "SJDI",
        "pub_year": 2025,
        "industry_tag": "미래전략도시,미래산업"
    }
]

# ==========================================
# [기능 구현 영역] 자동 분기 및 적재 엔진
# ==========================================

def check_pdf_type(pdf_path):
    """
    PDF 내부의 텍스트를 일부 추출하여 일반(텍스트) PDF인지 스캔본(이미지) PDF인지 판별합니다.
    """
    try:
        reader = PdfReader(pdf_path)
        total_text = ""
        # 상위 3페이지만 검사하여 글자가 전혀 안 읽히면 스캔본으로 판단 (속도 최적화)
        pages_to_check = min(3, len(reader.pages))
        for i in range(pages_to_check):
            text = reader.pages[i].extract_text()
            if text:
                total_text += text.strip()
        
        if len(total_text) > 10:
            return "TEXT"
        else:
            return "IMAGE"
    except Exception as e:
        print(f"[오류] PDF 타입 판별 실패: {e}")
        return "IMAGE"

def parse_with_clova_ocr(pdf_path):
    """
    텍스트가 없는 스캔본 PDF를 네이버 CLOVA OCR API를 사용해 구조화된 마크다운 형태로 변환합니다.
    """
    print(f" -> [상용 API 호출] 네이버 CLOVA OCR로 대용량 스캔본을 디지털 텍스트로 복원합니다...")
    
    headers = {
        "X-OCR-SECRET": CLOVA_OCR_SECRET
    }
    
    # CLOVA General OCR Spec에 맞는 요청 페이로드 구성
    payload = {
        "version": "V2",
        "requestId": "sejong_lab_parser",
        "timestamp": 0,
        "lang": "ko",
        "enableTableDetection": True # 복잡한 정책 통계 표 추출을 위해 필수 활성화
    }
    
    try:
        with open(pdf_path, "rb") as f:
            files = [
                ('file', (os.path.basename(pdf_path), f, 'application/pdf')),
                ('message', (None, re.sub(r'\s+', ' ', str(payload)), 'application/json'))
            ]
            response = requests.post(CLOVA_OCR_URL, headers=headers, files=files)
            
        if response.status_code == 200:
            # 받아온 영수증/문서 데이터에서 마크다운 및 텍스트 구조를 추출하는 가공부
            # (기본 응답 JSON 구조 파싱 - 실전 서비스 운영 시 데이터 레이아웃 세부 정제가 일어나는 곳입니다.)
            ocr_data = response.json()
            full_markdown = ""
            
            # CLOVA OCR이 추출해 준 문서 내 필드 결합
            for image in ocr_data.get("images", []):
                for field in image.get("fields", []):
                    full_markdown += field.get("inferText", "") + " "
                full_markdown += "\n\n"

                # tables JSON 객체 → 마크다운 테이블 변환 후 본문에 병합
                for table_md in preprocess_clova_tables_from_ocr({"images": [image]}):
                    full_markdown += table_md + "\n\n"

            return preprocess_markdown_tables(full_markdown)
        else:
            print(f"[오류] CLOVA OCR API 요청 실패 (코드: {response.status_code})")
            return None
    except Exception as e:
        print(f"[오류] CLOVA OCR 연동 중 예외 발생: {e}")
        return None

def _extract_clova_cell_text(cell):
    """CLOVA OCR 셀 JSON에서 인식 텍스트를 추출합니다."""
    text = (cell.get("inferText") or "").strip()
    if text:
        return text

    lines = []
    for text_line in cell.get("cellTextLines", []):
        words = [word.get("inferText", "") for word in text_line.get("cellWords", [])]
        line_text = "".join(words).strip()
        if line_text:
            lines.append(line_text)
    return " ".join(lines)

def _sanitize_markdown_cell(text):
    """마크다운 테이블 셀용 텍스트 정규화 (공백·파이프 이스케이프, 빈칸 대체)."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return "-"
    return re.sub(r"\|", r"\\|", text)

def convert_clova_table_to_markdown(table):
    """
    CLOVA OCR이 반환한 단일 table 객체(JSON)를 GitHub 스타일 마크다운 테이블로 변환합니다.
    rowIndex=가로열, columnIndex=세로행 기준으로 span을 펼쳐 빈칸 없는 직사각형 그리드를 만듭니다.
    """
    cells = table.get("cells", [])
    if not cells:
        fallback = re.sub(r"\s+", " ", (table.get("inferText") or "")).strip()
        return fallback

    max_row = 0
    max_col = 0
    for cell in cells:
        row = cell.get("columnIndex", 0)
        col = cell.get("rowIndex", 0)
        row_span = max(cell.get("columnSpan") or 1, 1)
        col_span = max(cell.get("rowSpan") or 1, 1)
        max_row = max(max_row, row + row_span)
        max_col = max(max_col, col + col_span)

    grid = [["" for _ in range(max_col)] for _ in range(max_row)]

    for cell in cells:
        text = _sanitize_markdown_cell(_extract_clova_cell_text(cell))
        row = cell.get("columnIndex", 0)
        col = cell.get("rowIndex", 0)
        row_span = max(cell.get("columnSpan") or 1, 1)
        col_span = max(cell.get("rowSpan") or 1, 1)
        for r in range(row, row + row_span):
            for c in range(col, col + col_span):
                if r < max_row and c < max_col and not grid[r][c]:
                    grid[r][c] = text

    for r in range(max_row):
        for c in range(max_col):
            if not grid[r][c]:
                grid[r][c] = "-"

    lines = []
    for i, row in enumerate(grid):
        lines.append("| " + " | ".join(row) + " |")
        if i == 0:
            lines.append("| " + " | ".join(["---"] * max_col) + " |")
    return "\n".join(lines)

def preprocess_clova_tables_from_ocr(ocr_data):
    """OCR 응답 JSON 전체에서 tables 배열을 읽어 마크다운 테이블 블록 리스트로 변환합니다."""
    markdown_tables = []
    for image in ocr_data.get("images", []):
        for table in image.get("tables", []):
            table_md = convert_clova_table_to_markdown(table)
            if table_md:
                markdown_tables.append(table_md)
    return markdown_tables

def preprocess_markdown_tables(markdown_text):
    """
    정규식 기반 마크다운 테이블 후처리.
    빈 셀을 '-'로 채우고, |---|---| 구분선이 없는 블록에는 자동 삽입합니다.
    """
    if not markdown_text:
        return markdown_text

    table_block_pattern = re.compile(r"(?:^\|.+\|\s*\n)+", re.MULTILINE)

    def _normalize_row(row):
        filled = re.sub(r"\|\s*(?=\|)", "| - ", row.rstrip())
        cells = [re.sub(r"\s+", " ", cell).strip() or "-" for cell in filled.strip("|").split("|")]
        return "| " + " | ".join(cells) + " |"

    def _fix_table_block(match):
        rows = [line for line in match.group(0).strip().splitlines() if line.strip()]
        if not rows:
            return match.group(0)

        normalized = [_normalize_row(row) for row in rows]
        col_count = normalized[0].count("|") - 1
        separator = "| " + " | ".join(["---"] * col_count) + " |"

        if len(normalized) == 1 or not re.match(r"^\|\s*[-:\s|]+\|\s*$", normalized[1]):
            normalized.insert(1, separator)

        return "\n".join(normalized) + "\n"

    return table_block_pattern.sub(_fix_table_block, markdown_text)

def parse_with_kordoc(pdf_path, md_path):
    """
    드래그가 가능한 일반형 PDF를 kordoc 오픈소스 CLI 엔진으로 무료 파싱합니다.
    """
    print(f" -> [무료 오픈소스 호출] 일반 텍스트형 PDF이므로 kordoc으로 즉시 구조화합니다...")
    result = subprocess.run(
        f'kordoc "{pdf_path}" -o "{md_path}"', 
        capture_output=True, 
        encoding="utf-8", 
        shell=True
    )
    if result.returncode == 0 and os.path.exists(md_path):
        with open(md_path, "r", encoding="utf-8") as f:
            return f.read()
    return None

def save_to_postgres(doc_id, report, markdown_content):
    """
    정제 작업이 완료된 마크다운 텍스트를 위계별로 쪼개어 Vercel Postgres DB에 적재합니다.
    """
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    # 중복 데이터 초기화 (재생성 구조)
    cursor.execute("DELETE FROM document_master WHERE doc_id = %s", (doc_id,))
    
    # 1. 마스터 테이블 등록
    cursor.execute("""
    INSERT INTO document_master (doc_id, doc_title, inst_code, pub_year, industry_tag)
    VALUES (%s, %s, %s, %s, %s)
    """, (doc_id, report["doc_title"], report["inst_code"], report["pub_year"], report["industry_tag"]))
    
    # 2. 본문 섹션별 분할 적재
    chunks = re.split(r'\n(?=#+ )', markdown_content)
    current_section = "개요 및 서론"
    
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = chunk.split('\n')
        first_line = lines[0]
        
        if first_line.startswith('#'):
            current_section = first_line.replace('#', '').strip()
            body_text = '\n'.join(lines[1:]).strip()
        else:
            body_text = chunk
            
        if body_text:
            cursor.execute("""
            INSERT INTO document_contents (doc_id, section_title, body_text)
            VALUES (%s, %s, %s)
            """, (doc_id, current_section, body_text))
            
    conn.commit()
    cursor.close()
    conn.close()

def main():
    print("=" * 60)
    print("🚀 [세종 인공지능 로컬 지식 랩] 1단계 파이프라인 가동")
    print("=" * 60)
    
    for report in sample_reports:
        pdf_path = report["filename"]
        doc_id = report["doc_id"]
        
        if not os.path.exists(pdf_path):
            print(f"\n[누락] 파일이 존재하지 않습니다: {pdf_path}")
            continue
            
        print(f"\n[분석 시작] 문서 일련번호: {doc_id} -> {pdf_path}")
        
        # STEP 1: 파일 유무 파악 및 형식 진단 분기
        pdf_type = check_pdf_type(pdf_path)
        print(f" -> 문서 형식 판별 결과: [{'스캔형 이미지 문서' if pdf_type == 'IMAGE' else '텍스트 선택형 일반 문서'}]")
        
        markdown_content = ""
        
        # STEP 2: 판별 결과에 따른 최적의 파싱 엔진 매핑 (비용 최적화)
        if pdf_type == "TEXT":
            md_path = pdf_path.replace(".pdf", ".md")
            markdown_content = parse_with_kordoc(pdf_path, md_path)
        else:
            markdown_content = parse_with_clova_ocr(pdf_path)
            
        # STEP 3: 가공된 구조화 데이터를 Vercel Postgres에 영구 적재
        if markdown_content:
            print(f" -> [DB 연동] 추출된 마크다운 데이터를 Vercel Postgres로 전송 중...")
            save_to_postgres(doc_id, report, markdown_content)
            print(f" -> [성공] 일련번호 {doc_id} 적재 완료.")
        else:
            print(f" -> [실패] 일련번호 {doc_id} 문서에서 데이터를 추출하지 못했습니다.")

if __name__ == "__main__":
    main()