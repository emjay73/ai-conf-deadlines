# ai-conf-deadlines

AI/CV/Graphics 학회 제출 마감 자동 캘린더.

매일 03:00 UTC (12:00 KST)에 [huggingface/ai-deadlines](https://github.com/huggingface/ai-deadlines)에서 최신 일정을 가져와 `docs/deadlines.ics`를 갱신합니다. 공식 발표가 없는 학회는 직전 사이클 패턴을 기준으로 `(tentative)` 표기해 추정 일정을 생성합니다.

## 관심 학회

CVPR, ICCV, ECCV, NeurIPS, ICLR, ICML, SIGGRAPH, SIGGRAPH Asia, AAAI, WACV, 3DV

## 포함하는 마감 종류

- Abstract 등록
- Paper 제출
- Supplementary 제출
- (WACV의 경우) Paper Registration

부가 트랙(art papers, posters, workshop proposals 등)은 제외합니다.

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

각 이벤트에 다음 알림이 포함됩니다:

- 7일 전
- 1일 전
- 1시간 전

알림이 너무 많으면 Google Calendar 설정에서 이 캘린더의 기본 알림을 조정하세요.

## 학회 추가/제거

`scripts/build_ics.py`의 `INTEREST` dict 또는 `EXTRA_CONFS` dict를 편집한 후 commit/push 하면 다음 워크플로우 실행 시 반영됩니다. 즉시 갱신하려면 Actions 탭 → "Update deadlines.ics" → "Run workflow".

## 수동 실행

```bash
pip install pyyaml
python scripts/build_ics.py
```

`docs/deadlines.ics`가 생성됩니다.
