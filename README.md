# ai-conf-deadlines

AI/CV/Graphics 학회 제출 마감 자동 캘린더.

매일 03:00 UTC (12:00 KST)에 [huggingface/ai-deadlines](https://github.com/huggingface/ai-deadlines)에서 최신 일정을 가져와 `docs/deadlines.ics`를 갱신합니다.

각 일정은 **KST 기준 종일(all-day) 이벤트**로 등록됩니다 — 사이클당 **접수 시작** 마커 1개 + 마감 종류별(abstract/paper/supplementary) **마감일** 마커. 마감일은 *KST 자정을 온전히 포함하는 마지막 날* 규칙을 따릅니다(= `마감시각 KST − 1일`의 날짜). 예: 3월 6일 15:00 마감 → 3월 5일, AoE 23:59 마감 → 해당 AoE 달력일. 마감 마커엔 7일·1일 전 알림이 붙습니다.

다음 사이클이 아직 업스트림 레포에 없으면, 학회 **공식 페이지를 직접 관찰**(`OFFICIAL_SOURCES`)해 날짜를 추출합니다 — 파싱한 날짜가 직전 사이클 추정치의 ±75일(`SANITY_WINDOW_DAYS`) 안이면 **확정(confirmed)**으로 신뢰하고, 못 긁거나 벗어나면 직전 사이클 패턴 기준 `(tentative)` 추정으로 폴백합니다. 공식 오픈일이 있으면 그날을 기간 시작점으로, 없으면 **마감 7일 전**을 시작점으로 추정합니다. 공식 페이지에 author registration 오픈일이 있으면 별도 등록 기간(registration open → abstract 마감)도 추가합니다.

현재 공식 페이지를 관찰하는 학회: **AAAI**(`aaai.org`), **NeurIPS·ICML**(`*.cc/Conferences/{year}/CallForPapers`), **CVPR·ICCV·ECCV**(`thecvf`/`ecva` `.../Dates`). 각 파서는 라이브 페이지(2025/2026 사이클)에 직접 돌려 abstract/paper/supplementary 날짜가 정확한지 검증한 뒤 등록했습니다.

> 공식 사이트를 새로 추가하려면 `scripts/build_ics.py`의 `OFFICIAL_SOURCES`에 `conf_id: {"url": lambda year: ...}` 한 줄을 넣으면 됩니다. 라벨이 날짜 앞이 아니라 뒤에 오는 사이트(AAAI류)는 `"layout": "date_first"`를 함께 지정하고, 표 구조가 특이하면 `"parser"`로 전용 파서를 붙일 수 있습니다.
>
> **제외**: ICLR(마감을 연도 없이 "Sep 19"로 표기 + 학회 전년도 마감이라 연도 추론 불가), WACV(연도별 서브도메인 + 멀티라운드), 로보틱스 ICRA/IROS/RSS/CoRL(연도-무관 단일 도메인이거나 산문형 일정이라 `url(year)` 패턴화·파싱이 불안정). 이들은 직전 사이클 추정(`(tentative)`)으로 남습니다.

## 관심 학회

- **AI/CV/Graphics**: CVPR, ICCV(홀수년), ECCV(짝수년), NeurIPS, ICLR, ICML, SIGGRAPH, SIGGRAPH Asia, AAAI, WACV, 3DV
- **Robotics**: ICRA, IROS, RSS, CoRL

## 포함하는 마감 종류

- Abstract 등록
- Paper 제출
- Supplementary 제출
- (WACV의 경우) Paper Registration
- **학회 개최 일정**: 본 행사 `개최 시작` / `개최 종료` (각각 별도 all-day 이벤트, KST)

부가 트랙(art papers, posters, workshop proposals 등)은 제외합니다. 워크숍/튜토리얼 개최일은 분리된 형태로 공개되지 않아(대개 'Workshops & Tutorials'로 묶여 자유 텍스트에만 존재) 별도 이벤트로 넣지 않고, 본 행사 시작·종료만 다룹니다.

## 구독 방법

### 1. GitHub Pages 활성화 (최초 1회)

repo 설정에서 Pages를 켭니다:

- Settings → Pages
- Source: `Deploy from a branch`
- Branch: `main` / `/docs`
- Save

활성화 후 ics URL은 다음과 같습니다:

```
https://<username>.github.io/ai-conf-deadlines/deadlines.ics
```

### 2. Google Calendar에 추가

1. [Google Calendar](https://calendar.google.com)에 접속 (PC 권장 — 모바일에서는 URL 구독 추가 불가)
2. 좌측 "다른 캘린더" 옆 `+` → "URL로 추가"
3. 위의 ics URL 입력 → "캘린더 추가"
4. 추가된 캘린더 이름을 "Conference Deadlines" 등으로 변경, 색상 지정

Google Calendar는 외부 ics URL을 약 24시간 주기로 자동 fetch합니다.

### 3. 안드로이드 위젯

폰의 Google Calendar 앱이 자동 동기화하므로, 홈 화면에 캘린더 위젯을 추가하면 항상 최신 일정이 보입니다.

- 홈 화면 길게 → 위젯 → 캘린더 → 위젯 선택
- 위젯 설정에서 "Conference Deadlines" 캘린더만 체크하면 다른 일정과 분리

## 알림

마감일 이벤트에 다음 알림이 포함됩니다(접수/등록 시작 마커에는 알림 없음):

- 7일 전
- 1일 전

알림이 너무 많으면 Google Calendar 설정에서 이 캘린더의 기본 알림을 조정하세요.

## 학회 추가/제거

`scripts/build_ics.py`의 `INTEREST` dict 또는 `EXTRA_CONFS` dict를 편집한 후 commit/push 하면 다음 워크플로우 실행 시 반영됩니다. 즉시 갱신하려면 Actions 탭 → "Update deadlines.ics" → "Run workflow".

## 수동 실행

```bash
pip install pyyaml
python scripts/build_ics.py
```

`docs/deadlines.ics`가 생성됩니다.
