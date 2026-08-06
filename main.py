# ============================================
# 🏭 고용노동부 안전 관련 공고 → 네이버 카페 자동 게시
# GitHub Actions 자동 실행 버전 (v1)
# 
# 대상:
#   1) 입법·행정예고 (lawmaking)
#   2) 훈령·예규·고시 (instruction)
# 검색어: "안전"
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

# 중복방지 저장소
STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)
STATE_FILE = STATE_DIR / "posted.json"

UPLOAD_INTERVAL_SEC = 25  # 네이버 연속 등록 방지

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# ============================================
# 🔧 상태 관리 (중복 방지)
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
# 🔧 인코딩 유틸
# ============================================
def naver_double_encode(text: str) -> str:
    """네이버 카페 API 한글 깨짐 방지 (이중 URL 인코딩)"""
    if not text:
        return ""
    return quote(quote(text, safe=''), safe='')

def to_html_entity(text: str) -> str:
    """본문용 - 특수문자 HTML 엔티티 변환"""
    result = []
    for c in text:
        code = ord(c)
        if code > 127 or c in '%&=?#':
            result.append(f"&#{code};")
        else:
            result.append(c)
    return ''.join(result)

def sanitize(text: str) -> str:
    """네이버 API 문제 특수문자 정리"""
    if not text:
        return ""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return text.strip()

def nl_to_br(text: str) -> str:
    return text.replace('\n', '<br>')

# ============================================
# 📥 목록 크롤링
# ============================================
def fetch_list(target: dict, keyword: str) -> list[dict]:
    """
    고용노동부 목록 페이지에서 '안전' 검색 결과 파싱
    반환: [{seq, title, dept, reg_date, view_url}, ...]
    """
    params = {
        "searchType": "title",     # 제목 검색
        "searchWrd": keyword,      # 검색어 (사이트가 다양한 파라미터 사용 → 아래서 폴백)
        "searchKeyword": keyword,
        "pageIndex": 1,
    }

    print(f"\n📥 [{target['name']}] 목록 요청: {target['list_url']} (검색어={keyword})")
    r = requests.get(target['list_url'], params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or 'utf-8'

    soup = BeautifulSoup(r.text, 'lxml')

    # 목록 테이블 탐색 (대부분의 정부 사이트는 <table class="board_list"> 또는 유사 구조)
    rows = []
    table = soup.select_one('table')
    if not table:
        print("   ⚠️ 테이블 없음")
        return []

    tbody = table.find('tbody') or table
    for tr in tbody.find_all('tr'):
        tds = tr.find_all('td')
        if len(tds) < 2:
            continue

        # 제목 셀 안의 <a> 찾기
        title_a = tr.find('a')
        if not title_a:
            continue

        title = title_a.get_text(strip=True)
        if not title:
            continue

        # 제목에 검색어가 없으면 스킵 (안전장치)
        if keyword not in title:
            continue

        # 상세 링크 (href 또는 onclick의 파라미터)
        href = title_a.get('href', '')
        onclick = title_a.get('onclick', '')

        seq = None
        view_url = None

        # 케이스 A: href에 이미 view URL
        if href and href not in ('#', 'javascript:;'):
            view_url = urljoin(target['list_url'], href)
            m = re.search(r'seq=(\d+)', view_url)
            if m:
                seq = m.group(1)

        # 케이스 B: onclick="fn_view('123')" 등
        if not seq and onclick:
            m = re.search(r"['\"](\d{3,})['\"]", onclick)
            if m:
                seq = m.group(1)
                view_url = f"{target['view_url']}?bbs_seq={seq}"

        if not seq:
            # 파라미터명 다양 → 모든 숫자 후보
            m = re.search(r'(\d{4,})', href + ' ' + onclick)
            if m:
                seq = m.group(1)
                if not view_url:
                    view_url = f"{target['view_url']}?bbs_seq={seq}"

        if not seq or not view_url:
            continue

        # 기타 컬럼: 부서/등록일 등 (열 위치는 게시판마다 다름 → 텍스트 전체에서 날짜 패턴 추출)
        row_text = ' | '.join(td.get_text(' ', strip=True) for td in tds)
        date_match = re.search(r'20\d{2}[-.\/]\d{1,2}[-.\/]\d{1,2}', row_text)
        reg_date = date_match.group(0) if date_match else ""

        rows.append({
            "seq": seq,
            "title": title,
            "reg_date": reg_date,
            "row_text": row_text,
            "view_url": view_url,
        })

    print(f"   ✅ 파싱된 항목: {len(rows)}건")
    return rows

# ============================================
# 📄 상세 페이지 크롤링
# ============================================
def fetch_detail(view_url: str) -> str:
    """상세 페이지 본문 텍스트 추출"""
    try:
        r = requests.get(view_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(r.text, 'lxml')

        # 흔한 본문 컨테이너 후보
        candidates = [
            'div.board_view', 'div.view_cont', 'div.bbs_view',
            'div.view_area', 'div.cont_area', 'div.contents',
            'td.contents', 'div.board-view', 'div#contents',
        ]
        body = None
        for sel in candidates:
            body = soup.select_one(sel)
            if body and len(body.get_text(strip=True)) > 50:
                break

        # 폴백: main 태그 또는 가장 긴 <div>
        if not body:
            body = soup.find('main') or soup.body

        # 첨부파일 목록 추출
        attachments = []
        for a in soup.select('a[href*="download"], a[href*="fileDown"], a[href*="file"]'):
            fname = a.get_text(strip=True)
            fhref = a.get('href', '')
            if fname and fhref and len(fname) < 200:
                full = urljoin(view_url, fhref)
                attachments.append((fname, full))

        # 본문 텍스트
        text = body.get_text('\n', strip=True) if body else ""
        # 과도한 공백 정리
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)

        # 첨부파일 섹션
        if attachments:
            text += "\n\n[첨부파일]\n"
            for fname, furl in attachments[:20]:
                text += f"- {fname}\n  ({furl})\n"

        return sanitize(text)
    except Exception as e:
        print(f"   ⚠️ 상세 페이지 실패: {e}")
        return ""

# ============================================
# 📝 카페 본문 생성
# ============================================
def build_content(item: dict, target: dict, body_text: str) -> str:
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst).strftime("%Y-%m-%d %H:%M")

    parts = [
        f"<h3>{item['title']}</h3>",
        "<p>",
        f"<b>📂 구분:</b> 고용노동부 · {target['name']}<br>",
        f"<b>📅 등록일:</b> {item.get('reg_date') or '-'}<br>",
        f"<b>🔖 자료번호:</b> {item['seq']}",
        "</p>",
        "<hr>",
        "<p><b>📄 원문 내용</b></p>",
        f"<p>{nl_to_br(body_text) if body_text else '(본문을 불러오지 못했습니다. 아래 원문 링크에서 확인해 주세요.)'}</p>",
        "<hr>",
        f"<p>👉 <a href='{item['view_url']}' target='_blank'><b>고용노동부 원문 바로가기</b></a></p>",
        f"<p>👉 <a href='{target['list_url']}' target='_blank'>{target['name']} 목록 페이지</a></p>",
        "<hr>",
        f"<p><small>🤖 고용노동부 정보공개 자동 수집 · {now} KST</small></p>",
    ]
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
    """
    네이버 카페 글 등록 (urlencoded + 이중 인코딩)
    """
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

# ============================================
# 🚀 메인
# ============================================
def run():
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    print("=" * 60)
    print("🏭 고용노동부 안전 공고 → 네이버 카페 자동 게시")
    print(f"⏰ {now.strftime('%Y-%m-%d %H:%M KST')}")
    print("=" * 60)

    # 환경변수 체크
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

    # 토큰
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

        # 신규만 필터
        new_items = [it for it in items if it['seq'] not in state[tag]]
        print(f"   🆕 신규: {len(new_items)}건 / 전체: {len(items)}건")
        total_new += len(new_items)

        # 오래된 것부터 등록 (목록은 대개 최신순 → 역순)
        for idx, item in enumerate(reversed(new_items)):
            print(f"\n   ▶ [{idx+1}/{len(new_items)}] {item['title'][:50]}")
            print(f"      URL: {item['view_url']}")

            body_text = fetch_detail(item['view_url'])
            print(f"      본문: {len(body_text)}자")

            subject = f"[고용노동부·{target['name']}] {item['title']}"
            content = build_content(item, target, body_text)

            try:
                r = post_to_cafe(token, subject, content)
                if r.status_code in (200, 201):
                    resp = r.json()
                    msg = resp.get("message", {})
                    if msg.get("status") and str(msg.get("status")) != "200":
                        raise RuntimeError(f"API error: {msg}")
                    art_url = msg.get("result", {}).get("articleUrl", "")
                    print(f"      ✅ 게시 성공: {art_url}")
                    state[tag].append(item['seq'])
                    save_state(state)  # 매 건마다 저장 (중복 방지)
                    total_ok += 1
                else:
                    print(f"      ❌ HTTP {r.status_code}: {r.text[:200]}")
                    total_fail += 1
            except Exception as e:
                print(f"      ❌ 예외: {e}")
                total_fail += 1

            # 마지막 아니면 대기 (네이버 연속등록 차단 회피)
            if idx < len(new_items) - 1:
                print(f"      ⏳ {UPLOAD_INTERVAL_SEC}초 대기...")
                time.sleep(UPLOAD_INTERVAL_SEC)

        # 카테고리 간에도 잠깐 쉼
        time.sleep(5)

    print("\n" + "=" * 60)
    print(f"🎉 완료 — 신규감지: {total_new} · 성공: {total_ok} · 실패: {total_fail}")
    print("=" * 60)

    if total_fail > 0 and total_ok == 0:
        # 실패만 있으면 CI 실패로
        exit(1)

if __name__ == "__main__":
    run()
