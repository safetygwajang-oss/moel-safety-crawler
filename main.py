# ============================================
# 🏭 고용노동부 안전 관련 공고 → 네이버 카페 자동 게시
# GitHub Actions 자동 실행 버전 (v2.3 - 네트워크 재시도 + 본문 정돈)
# ============================================

import os
import re
import json
import time
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urljoin
from bs4 import BeautifulSoup

# ============================================
# ⚙️ 환경변수
# ============================================
CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("NAVER_REFRESH_TOKEN", "")
CAFE_ID       = os.environ.get("CAFE_ID", "31767633")
MENU_ID       = os.environ.get("MENU_ID", "13")

# ============================================
# 📌 크롤링 대상
# ============================================
MOEL_BASE = "https://www.moel.go.kr"

TARGETS = [
    {
        "name": "입법·행정예고",
        "tag": "입법행정예고",
        "list_url": f"{MOEL_BASE}/info/lawinfo/lawmaking/list.do",
        "view_url": f"{MOEL_BASE}/info/lawinfo/lawmaking/view.do",
    },
    {
        "name": "훈령·예규·고시",
        "tag": "훈령예규고시",
        "list_url": f"{MOEL_BASE}/info/lawinfo/instruction/list.do",
        "view_url": f"{MOEL_BASE}/info/lawinfo/instruction/view.do",
    },
    {
        "name": "최근 제·개정 법령",
        "tag": "제개정법령",
        "list_url": f"{MOEL_BASE}/info/lawinfo/revision/list.do",
        "view_url": f"{MOEL_BASE}/info/lawinfo/revision/view.do",
    },
]

SEARCH_KEYWORD = "안전"

STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)
STATE_FILE = STATE_DIR / "posted.json"

UPLOAD_INTERVAL_SEC = 25
RETRY_WAIT_SEC = 30

# 🆕 MOEL 서버 요청 재시도 설정
HTTP_MAX_RETRIES = 4        # 최대 4번 시도
HTTP_RETRY_WAIT = 15        # 실패 시 15초 대기
HTTP_TIMEOUT = (15, 45)     # (연결 타임아웃, 읽기 타임아웃)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
}

# 🆕 세션 재사용
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ============================================
# 🆕 재시도 래퍼
# ============================================
def http_get(url: str, params: dict = None) -> requests.Response:
    """MOEL 서버가 불안정해서 여러 번 재시도"""
    last_err = None
    for attempt in range(1, HTTP_MAX_RETRIES + 1):
        try:
            r = SESSION.get(url, params=params, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            return r
        except Exception as e:
            last_err = e
            print(f"      ⚠️ HTTP 시도 {attempt}/{HTTP_MAX_RETRIES} 실패: {type(e).__name__}")
            if attempt < HTTP_MAX_RETRIES:
                print(f"      ⏳ {HTTP_RETRY_WAIT}초 후 재시도...")
                time.sleep(HTTP_RETRY_WAIT)
    raise last_err

# ============================================
# 🔧 상태 관리
# ============================================
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

# ============================================
# 🔧 인코딩·정돈 유틸
# ============================================
def naver_double_encode(text: str) -> str:
    if not text:
        return ""
    return quote(quote(text, safe=''), safe='')

def to_html_entity(text: str) -> str:
    result = []
    for c in text:
        code = ord(c)
        if code > 127 or c in '%&=?#':
            result.append(f"&#{code};")
        else:
            result.append(c)
    return ''.join(result)

def sanitize(text: str) -> str:
    if not text:
        return ""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return text.strip()

def nl_to_br(text: str) -> str:
    return text.replace('\n', '<br>')

def clean_title(text: str) -> str:
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()

# ============================================
# 🆕 본문 줄바꿈 정돈
# ============================================
def tidy_body(text: str) -> str:
    """본문의 이상한 줄바꿈을 다듬어 문장 단위로 자연스럽게"""
    if not text:
        return ""

    one = re.sub(r'\s+', ' ', text.replace('\n', ' ')).strip()

    # 한글 종결어미 + 마침표 뒤에서 개행 (뒤가 숫자면 개행 안 함 = 날짜 보호)
    endings = ['습니다', '됩니다', '입니다', '있습니다', '없습니다',
               '하였다', '되었다', '이다', '한다', '된다',
               '다', '요', '함', '음', '임', '됨']
    endings.sort(key=len, reverse=True)

    for e in endings:
        pattern = re.escape(e) + r'\.\s+(?=[^\d\s])'
        one = re.sub(pattern, e + '.\n', one)

    # 물음표/느낌표 뒤
    one = re.sub(r'([!?])\s+(?=[^\d\s])', r'\1\n', one)

    # 특정 키워드 앞 개행
    one = re.sub(r'\s+(?=붙임)', '\n', one)
    one = re.sub(r'\s+(?=공포\s*:)', '\n', one)
    one = re.sub(r'\s+(?=시행\s*:)', '\n', one)
    one = re.sub(r'\s+(?=\*\s)', '\n', one)

    lines = [ln.strip() for ln in one.split('\n')]
    lines = [ln for ln in lines if ln]

    return '\n'.join(lines).strip()

# ============================================
# 🧹 본문 노이즈 제거
# ============================================
NOISE_KEYWORDS = [
    "홈", "으로 이동", "정보공개", "예산·법령정보",
    "최근 제·개정 법령", "입법·행정예고", "훈령·예규·고시",
    "인쇄하기", "인쇄", "목록", "이전글", "다음글", "이전", "다음",
    "공유하기", "페이스북", "트위터", "카카오", "네이버 블로그",
    "URL 복사", "본문 바로가기", "메뉴 바로가기",
    "이 누리집은", "공식 누리집", "누리집 안내지도",
    "통합검색", "최근검색어", "검색어 자동완성",
    "상단으로 이동", "국민이 주인인 나라", "함께 행복한 대한민국",
    "국민 누구나 원하는",
]

def is_noise_line(line: str) -> bool:
    s = line.strip()
    if not s or len(s) <= 1:
        return True
    for kw in NOISE_KEYWORDS:
        if s == kw:
            return True
        if len(s) < 20 and kw in s:
            return True
    return False

# ============================================
# 📥 목록 크롤링
# ============================================
def fetch_list(target: dict, keyword: str) -> list[dict]:
    params = {
        "searchType": "title",
        "searchWrd": keyword,
        "searchKeyword": keyword,
        "pageIndex": 1,
    }

    print(f"\n📥 [{target['name']}] 목록 요청: {target['list_url']} (검색어={keyword})")
    r = http_get(target['list_url'], params=params)   # 🆕 재시도 래퍼 사용
    r.encoding = r.apparent_encoding or 'utf-8'

    soup = BeautifulSoup(r.text, 'lxml')

    table = (
        soup.select_one('table.board_list')
        or soup.select_one('div.board_list table')
        or soup.select_one('div.tbl_list table')
        or soup.select_one('table')
    )
    if not table:
        print("   ⚠️ 테이블 없음")
        return []

    rows = []
    tbody = table.find('tbody') or table
    for tr in tbody.find_all('tr'):
        tds = tr.find_all('td')
        if len(tds) < 2:
            continue

        title_a = tr.find('a')
        if not title_a:
            continue

        title = clean_title(title_a.get_text(strip=True))
        if not title:
            continue

        if keyword not in title:
            continue

        href = title_a.get('href', '')
        onclick = title_a.get('onclick', '')

        seq = None
        view_url = None

        if href and href not in ('#', 'javascript:;'):
            view_url = urljoin(target['list_url'], href)
            m = re.search(r'seq=(\d+)', view_url)
            if m:
                seq = m.group(1)

        if not seq and onclick:
            m = re.search(r"['\"](\d{3,})['\"]", onclick)
            if m:
                seq = m.group(1)
                view_url = f"{target['view_url']}?bbs_seq={seq}"

        if not seq:
            m = re.search(r'(\d{4,})', href + ' ' + onclick)
            if m:
                seq = m.group(1)
                if not view_url:
                    view_url = f"{target['view_url']}?bbs_seq={seq}"

        if not seq or not view_url:
            continue

        row_text = ' | '.join(td.get_text(' ', strip=True) for td in tds)
        date_match = re.search(r'20\d{2}[-.\/]\d{1,2}[-.\/]\d{1,2}', row_text)
        reg_date = date_match.group(0) if date_match else ""

        rows.append({
            "seq": seq,
            "title": title,
            "reg_date": reg_date,
            "view_url": view_url,
        })

    print(f"   ✅ 파싱된 항목: {len(rows)}건")
    return rows

# ============================================
# 📄 상세 - 메타테이블
# ============================================
def extract_meta_table(soup: BeautifulSoup):
    meta = {}
    target_table = None
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if not rows:
            continue
        picked = {}
        for tr in rows:
            ths = tr.find_all('th')
            tds = tr.find_all('td')
            for th, td in zip(ths, tds):
                k = re.sub(r'\s+', ' ', th.get_text(strip=True))
                v = re.sub(r'\s+', ' ', td.get_text(' ', strip=True))
                if k and v:
                    picked[k] = v
        if any(key in picked for key in ('제목', '등록일', '담당부서', '유형')):
            meta = picked
            target_table = table
            break
    return meta, target_table

# ============================================
# 📄 상세 - 본문
# ============================================
def extract_body_text(soup: BeautifulSoup, meta_table) -> str:
    for tag in soup.select(
        'header, footer, nav, script, style, aside, '
        '.snb, .lnb, .gnb, .location, .breadcrumb, '
        '.print, .btn_area, .board_btm, .paging'
    ):
        tag.decompose()

    if meta_table:
        meta_table.decompose()

    for tag in soup.select('.file_list, .file_area, .attach, .attachment, .file, .filedown'):
        tag.decompose()

    candidates = [
        'div.board_view', 'div.view_cont', 'div.bbs_view',
        'div.view_area', 'div.cont_area', 'div.view_content',
        'div.board-view', 'div.bbs_content', 'div.contents_area',
    ]
    body = None
    for sel in candidates:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 30:
            body = el
            break

    if not body:
        body = soup.find('main') or soup.body

    if not body:
        return ""

    raw = body.get_text('\n', strip=True)

    lines = []
    seen = set()
    for line in raw.split('\n'):
        line = re.sub(r'[ \t]+', ' ', line).strip()
        if is_noise_line(line):
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)

    text = '\n'.join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# ============================================
# 📄 상세 - 첨부파일 (dedup)
# ============================================
def extract_attachments(soup: BeautifulSoup, base_url: str):
    seen_urls = set()
    seen_names = set()
    result = []

    for a in soup.find_all('a'):
        href = a.get('href', '')
        onclick = a.get('onclick', '')
        text = a.get_text(strip=True)

        if not text:
            continue

        if text in ("첨부", "첨부파일", "다운로드", "바로보기", "미리보기", "보기"):
            continue

        if not re.search(
            r'\.(pdf|hwp|hwpx|doc|docx|xls|xlsx|ppt|pptx|zip|jpg|jpeg|png|gif|txt)(\s|$|\))',
            text, re.I
        ):
            continue

        file_url = None
        if href and ('download' in href.lower() or 'fileDown' in href or 'file' in href.lower()):
            file_url = urljoin(base_url, href)
        elif onclick:
            m = re.search(r"['\"]([^'\"]*(?:file|download)[^'\"]*)['\"]", onclick, re.I)
            if m:
                file_url = urljoin(base_url, m.group(1))

        if not file_url:
            continue

        norm_name = re.sub(r'\s+', ' ', text).strip()
        if file_url in seen_urls or norm_name in seen_names:
            continue
        seen_urls.add(file_url)
        seen_names.add(norm_name)

        result.append((norm_name, file_url))

    return result

# ============================================
# 📄 상세 통합
# ============================================
def fetch_detail(view_url: str) -> dict:
    try:
        r = http_get(view_url)   # 🆕 재시도 래퍼 사용
        r.encoding = r.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(r.text, 'lxml')

        attachments = extract_attachments(soup, view_url)
        meta, meta_table = extract_meta_table(soup)
        body = extract_body_text(soup, meta_table)
        body = tidy_body(body)   # 🆕 본문 정돈

        return {
            "meta": meta,
            "body": sanitize(body),
            "attachments": attachments,
        }
    except Exception as e:
        print(f"   ⚠️ 상세 페이지 실패: {e}")
        return {"meta": {}, "body": "", "attachments": []}

# ============================================
# 📝 카페 본문 생성
# ============================================
def build_content(item: dict, target: dict, detail: dict) -> str:
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst).strftime("%Y-%m-%d %H:%M")

    meta = detail.get("meta", {})
    body = detail.get("body", "")
    attachments = detail.get("attachments", [])

    meta_order = ["제목", "유형", "담당부서", "전화번호", "담당자", "등록일"]
    meta_lines = []
    for key in meta_order:
        if key in meta and meta[key]:
            meta_lines.append(f"<b>{key}:</b> {meta[key]}")
    for k, v in meta.items():
        if k not in meta_order and v:
            meta_lines.append(f"<b>{k}:</b> {v}")

    if not meta_lines:
        meta_lines.append(f"<b>제목:</b> {item['title']}")
        if item.get('reg_date'):
            meta_lines.append(f"<b>등록일:</b> {item['reg_date']}")

    meta_html = "<br>".join(meta_lines)

    parts = [
        f"<h3>{item['title']}</h3>",
        f"<p><b>📂 구분:</b> 고용노동부 · {target['name']}</p>",
        "<hr>",
        "<p><b>📋 상세정보</b></p>",
        f"<p>{meta_html}</p>",
        "<hr>",
        "<p><b>📄 본문</b></p>",
        f"<p>{nl_to_br(body) if body else '(본문을 불러오지 못했습니다. 아래 원문 링크에서 확인해 주세요.)'}</p>",
    ]

    if attachments:
        parts.append("<hr>")
        parts.append(f"<p><b>📎 첨부파일 ({len(attachments)}건)</b></p>")
        parts.append("<ul>")
        for name, url in attachments:
            parts.append(f"<li><a href='{url}' target='_blank'>{name}</a></li>")
        parts.append("</ul>")

    parts.extend([
        "<hr>",
        f"<p>👉 <a href='{item['view_url']}' target='_blank'><b>고용노동부 원문 바로가기</b></a></p>",
        f"<p><small>🤖 고용노동부 정보공개 자동 수집 · {now} KST</small></p>",
    ])

    return "\n".join(parts)

# ============================================
# 🔑 네이버 카페 API
# ============================================
def get_access_token() -> str:
    url = "https://nid.naver.com/oauth2.0/token"
    params = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError(f"토큰 발급 실패: {r.text}")
    return token

def post_to_cafe(token: str, subject: str, content: str) -> requests.Response:
    url = f"https://openapi.naver.com/v1/cafe/{CAFE_ID}/menu/{MENU_ID}/articles"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
    }
    subject_enc = naver_double_encode(subject)
    content_html = to_html_entity(content)
    content_enc = quote(content_html)

    body = f"subject={subject_enc}&content={content_enc}&openyn=true"
    r = requests.post(url, headers=headers, data=body, timeout=60)
    return r

def post_with_retry(token: str, subject: str, content: str, max_attempts: int = 2):
    last_err = ""
    for attempt in range(1, max_attempts + 1):
        try:
            r = post_to_cafe(token, subject, content)
            if r.status_code in (200, 201):
                resp = r.json()
                msg = resp.get("message", {})
                status = str(msg.get("status", "200"))
                if status not in ("200", ""):
                    err = msg.get("error", {})
                    last_err = f"API status={status} {err}"
                    print(f"      ⚠️ 시도 {attempt}/{max_attempts} - {last_err}")
                else:
                    art_url = msg.get("result", {}).get("articleUrl", "")
                    return True, art_url, ""
            else:
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                print(f"      ⚠️ 시도 {attempt}/{max_attempts} - {last_err}")
        except Exception as e:
            last_err = str(e)
            print(f"      ⚠️ 시도 {attempt}/{max_attempts} 예외 - {last_err}")

        if attempt < max_attempts:
            print(f"      ⏳ {RETRY_WAIT_SEC}초 후 재시도...")
            time.sleep(RETRY_WAIT_SEC)

    return False, "", last_err

# ============================================
# 🚀 메인
# ============================================
def run():
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    print("=" * 60)
    print("🏭 고용노동부 안전 공고 → 네이버 카페 자동 게시 (v2.3)")
    print(f"⏰ {now.strftime('%Y-%m-%d %H:%M KST')}")
    print("=" * 60)

    missing = [k for k, v in {
        "NAVER_CLIENT_ID": CLIENT_ID,
        "NAVER_CLIENT_SECRET": CLIENT_SECRET,
        "NAVER_REFRESH_TOKEN": REFRESH_TOKEN,
    }.items() if not v]
    if missing:
        print(f"❌ 환경변수 누락: {missing}")
        return

    state = load_state()
    print(f"📚 기존 게시 이력: {sum(len(v) for v in state.values())}건")

    print("\n🔑 액세스 토큰 발급...")
    token = get_access_token()
    print("   ✅ 토큰 OK")

    total_new = 0
    total_ok = 0
    total_fail = 0

    for target in TARGETS:
        tag = target['tag']
        state.setdefault(tag, [])

        try:
            items = fetch_list(target, SEARCH_KEYWORD)
        except Exception as e:
            print(f"   ❌ 목록 실패: {e}")
            continue

        new_items = [it for it in items if it['seq'] not in state[tag]]
        print(f"   🆕 신규: {len(new_items)}건 / 전체: {len(items)}건")
        total_new += len(new_items)

        for idx, item in enumerate(reversed(new_items)):
            print(f"\n   ▶ [{idx+1}/{len(new_items)}] {item['title'][:60]}")
            print(f"      URL: {item['view_url']}")

            detail = fetch_detail(item['view_url'])
            print(f"      본문: {len(detail['body'])}자 · 첨부: {len(detail['attachments'])}건")

            subject = f"[고용노동부·{target['name']}] {item['title']}"
            content = build_content(item, target, detail)

            success, art_url, err = post_with_retry(token, subject, content, max_attempts=2)
            if success:
                print(f"      ✅ 게시 성공: {art_url}")
                state[tag].append(item['seq'])
                save_state(state)
                total_ok += 1
            else:
                print(f"      ❌ 최종 실패: {err}")
                total_fail += 1

            if idx < len(new_items) - 1:
                print(f"      ⏳ {UPLOAD_INTERVAL_SEC}초 대기...")
                time.sleep(UPLOAD_INTERVAL_SEC)

        time.sleep(5)

    print("\n" + "=" * 60)
    print(f"🎉 완료 — 신규감지: {total_new} · 성공: {total_ok} · 실패: {total_fail}")
    print("=" * 60)

    if total_new > 0 and total_ok == 0 and total_fail > 0:
        exit(1)

if __name__ == "__main__":
    run()
